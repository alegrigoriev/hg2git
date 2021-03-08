#   Copyright 2021-2023 Alexandre Grigoriev
#
#   Licensed under the Apache License, Version 2.0 (the "License");
#   you may not use this file except in compliance with the License.
#   You may obtain a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#   Unless required by applicable law or agreed to in writing, software
#   distributed under the License is distributed on an "AS IS" BASIS,
#   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#   See the License for the specific language governing permissions and
#   limitations under the License.

from __future__ import annotations
from typing import Iterator

import io
from pathlib import Path
import shutil
from types import SimpleNamespace
import git_repo

from history_reader import *
from lookup_tree import *
from rev_ranges import *
import project_config

def parse_name_email(name):
	if m := re.fullmatch('([^<>]+?) +<([^<>@]+?(?:@| at | AT )[^<>@]+)>', name):
		name = m[1]
		email = m[2]
		email = re.sub(' at | AT ', '@', email)
		email = re.sub(' dot | DOT ', '.', email)
		if m := re.fullmatch('"([^"]+)"', name):
			name = m[1]
	elif m := re.fullmatch('([^<>@]+)@[^<>@]+', name):
		name = m[1]
		email = m[0]
	elif m := re.fullmatch('.+? +([^ ]+)', name):
		name = m[0]
		email = m[1] + "@localhost"
	else:
		email = name + "@localhost"

	return name, email

class author_props:
	def __init__(self, author, email):
		self.author = author
		self.email = email
		return

	def __str__(self):
		return "%s <%s>" % (self.author, self.email)

def log_to_paragraphs(log):
	# Split log message to paragraphs
	paragraphs = []
	log = log.replace('\r\n', '\n')
	if log.startswith('\n\n'):
		paragraphs.append('')

	log = log.strip('\n \t')
	for paragraph in log.split('\n\n'):
		paragraph = paragraph.rstrip(' \t').lstrip('\n')
		if paragraph:
			paragraphs.append(paragraph)
	return paragraphs

class revision_props:
	def __init__(self, revision, log, author_info, date):
		self.revision = revision
		self.log = log
		self.author_info = author_info
		self.date = date
		return

### project_branch_rev keeps result for a processed revision
class project_branch_rev:
	def __init__(self, branch:project_branch, prev_rev=None):
		self.rev = None
		self.branch = branch
		self.log_file = branch.proj_tree.log_file
		self.commit = None
		self.rev_commit = None
		self.staged_git_tree = None
		self.committed_git_tree = None
		self.committed_tree = None
		self.staged_tree:git_tree = None
		# Next commit in history
		self.next_rev = None
		self.prev_rev = prev_rev
		# revisions_to_merge is a map of revisions pending to merge, keyed by branch.
		self.revisions_to_merge = None
		# any_changes_present is set to true if stagelist was not empty
		self.any_changes_present = False
		if prev_rev is None:
			self.tree:git_tree = None
			self.merged_revisions = {}
		else:
			prev_rev.next_rev = self
			self.tree:git_tree = prev_rev.tree
			# merged_revisions is a map of merged revisions keyed by branch.
			# It either refers to the previous revision's map,
			# or a copy is made and modified
			self.merged_revisions = prev_rev.merged_revisions

		# list of rev-info the commit on this revision would depend on - these are parent revs for the rev's commit
		self.parents = []
		self.cherry_pick_revs = []
		self.props_list = []
		self.tags = None
		return

	def set_revision(self, revision):
		self.tree = revision.tree
		if self.tree is None:
			return None

		self.rev = revision.rev
		self.rev_id = revision.rev_id
		self.add_revision_props(revision)

		return self

	def get_cherrypick_str(self):
		cherry_pick_msg = []
		# Sort by ascending revision number
		self.cherry_pick_revs.sort(key=lambda rev_info : rev_info.rev)
		# Commit list without duplicates
		cherry_pick_commits = {}
		for rev_info in self.cherry_pick_revs:
			if rev_info.rev_commit is None:
				continue

			if self.is_merged_from(rev_info):
				continue

			change_id = rev_info.change_id
			if rev_info.commit not in cherry_pick_commits:
				cherry_pick_commits[rev_info.commit] = rev_info

		if len(cherry_pick_commits) == 1:
			# Make the new commit inherit Change-Id
			self.change_id = change_id

		for rev_info in cherry_pick_commits.values():
			refname = re.sub('(?:^refs/(?:heads/)?)(.*)?', r'\1', rev_info.branch.refname)
			if not refname:
				refname = rev_info.branch.name

			cherry_pick_msg.append("Cherry-picked-from: %s %s;%d" % (rev_info.commit, refname, rev_info.rev))
			if rev_info.change_id != self.change_id:
				cherry_pick_msg[-1] += " Change-Id: %s" % (rev_info.change_id)

		return '\n'.join(cherry_pick_msg)

	### The function returns a single revision_props object, with:
	# .log assigned a list of text paragraphs,
	# .author, date, email, revision assigned from first revision_props
	def get_combined_revision_props(self, base_rev=None, decorate_revision_id=False):
		props_list = self.props_list
		if not props_list:
			return None

		prop0 = props_list[0]
		msg = prop0.log.copy()

		if not msg:
			msg = self.make_change_description(base_rev)
		elif msg and not msg[0]:
			msg[0] = self.make_change_description(base_rev)[0]

		if not msg or decorate_revision_id:
			msg.append("HG-revision: %s" % self.rev)

		return revision_props(prop0.revision, msg, prop0.author_info, prop0.date)

	def get_commit_revision_props(self, base_rev):
		decorate_revision_id=getattr(self.branch.proj_tree.options, 'decorate_revision_id', False)
		props = self.get_combined_revision_props(base_rev, decorate_revision_id=decorate_revision_id)

		cherry_pick_msg = self.get_cherrypick_str()
		if cherry_pick_msg:
			props.log.append(cherry_pick_msg)

		return props

	### The function sets or adds the revision properties for the upcoming commit
	def add_revision_props(self, revision):
		props_list = self.props_list
		if props_list and props_list[0].revision is revision:
			# already there
			return

		log = revision.log
		if revision.author:
			author_info = author_props(*parse_name_email(revision.author))
		else:
			# git commit-tree barfs if author is not provided
			author_info = author_props("(None)", "none@localhost")

		date = str(revision.datetime)

		for edit_msg in self.branch.edit_msg_list:
			if edit_msg.revs:
				if not rev_in_ranges(edit_msg.revs, self.rev):
					continue
			if edit_msg.rev_ids and not self.rev_id in edit_msg.rev_ids:
				continue
			log, count = edit_msg.match.subn(edit_msg.replace, log, edit_msg.max_sub)
			if count and edit_msg.final:
				break
			continue

		props_list.insert(0,
				revision_props(revision, log_to_paragraphs(log), author_info, date))
		return

	def make_change_description(self, base_rev):
		# Don't make a description if the base revision is an imported commit from and appended repo
		if base_rev is None:
			base_tree = None
			base_branch = None
		elif base_rev.tree is not None or base_rev.commit is None:
			base_tree = base_rev.committed_tree
			base_branch = base_rev.branch
		else:
			return []

		added_files = []
		changed_files = []
		deleted_files = []
		added_dirs = []
		deleted_dirs = []
		# staged_tree could be None. Invoke the comparison in reverse order,
		# and swap the result
		for t in self.tree.compare(base_tree):
			path = t[0]
			obj2 = t[1]
			obj1 = t[2]

			if obj1 is None:
				# added items
				if obj2.is_dir():
					added_dirs.append((path, obj2))
				else:
					added_files.append((path, obj2))
				continue
			if obj2 is None:
				# deleted items
				if base_branch is not None \
					and base_branch.ignore_file(path):
					continue

				if obj1.is_dir():
					deleted_dirs.append((path, obj1))
				else:
					deleted_files.append((path, obj1))
				continue
			
			if obj1.is_file():
				changed_files.append(path)
			continue

		# Find renamed directories
		renamed_dirs = []
		for new_path, tree2 in added_dirs:
			# Find similar tree in deleted_dirs
			for t in deleted_dirs:
				old_path, tree1 = t
				metrics = tree2.get_difference_metrics(tree1)
				if metrics.added + metrics.deleted < metrics.identical + metrics.different:
					renamed_dirs.append((old_path, new_path))
					deleted_dirs.remove(t)
					for t in deleted_files.copy():
						if t[0].startswith(old_path):
							deleted_files.remove(t)
					for t in added_files.copy():
						if t[0].startswith(new_path):
							added_files.remove(t)
					break
				continue
			continue

		# Find renamed files
		renamed_files = []
		for t2 in added_files.copy():
			# Find similar tree in deleted_dirs
			new_path, file2 = t2
			for t1 in deleted_files:
				old_path, file1 = t1
				# Not considering renames of empty files
				if file1.data and file1.data_sha1 == file2.data_sha1:
					renamed_files.append((old_path, new_path))
					added_files.remove(t2)
					deleted_files.remove(t1)
					break
				continue
			continue

		title = ''
		long_title = ''
		if added_files:
			if title:
				title += ', added files'
				long_title += ', added ' + ', '.join((path for path, file1 in added_files))
			else:
				title = 'Added files'
				long_title += 'Added ' + ', '.join((path for path, file1 in added_files))

		if deleted_files:
			if title:
				title += ', deleted files'
				long_title += ', deleted ' + ', '.join((path for path, file1 in deleted_files))
			else:
				title = 'Deleted files'
				long_title += 'Deleted ' + ', '.join((path for path, file1 in deleted_files))

		if changed_files:
			if title:
				title += ', changed files'
				long_title += ', changed ' + ', '.join(changed_files)
			else:
				title = 'Changed files'
				long_title += 'Changed ' + ', '.join(changed_files)

		if renamed_files or renamed_dirs:
			if title:
				long_title += ', renamed ' + ', '.join(("%s to %s" % (old_path, new_path) for old_path, new_path in (*renamed_dirs,*renamed_files)))
			else:
				long_title += 'Renamed ' + ', '.join(("%s to %s" % (old_path, new_path) for old_path, new_path in (*renamed_dirs,*renamed_files)))

		if len(long_title) < 100:
			return [long_title]

		if renamed_files:
			if title:
				title += ', renamed files'
			else:
				title = 'Renamed files'

		if renamed_dirs:
			if title:
				title += ', renamed directories'
			else:
				title = 'Renamed directories'

		log = []
		for path, file1 in added_files:
			log.append("Added file: %s" % (path))

		for path, file1 in deleted_files:
			log.append("Deleted file: %s" % (path))

		for path in changed_files:
			log.append("Changed file: %s" % (path))

		for old_path, new_path in renamed_files:
			log.append("Renamed file: %s to: %s" % (old_path, new_path))

		for old_path, new_path in renamed_dirs:
			log.append("Renamed directory: %s to: %s" % (old_path, new_path))

		if len(log) <= 1:
			return log

		return [title, '\n'.join(log)]

	def add_parent_revision(self, add_rev):
		if add_rev.tree is None:
			return

		if self.is_merged_from(add_rev):
			return

		if self.revisions_to_merge is None:
			self.revisions_to_merge = {}
		else:
			# Check if this revision or its descendant has been added for merge already
			merged_rev = self.revisions_to_merge.get(add_rev.branch)
			if merged_rev is not None and merged_rev.rev >= add_rev.rev:
				return

		self.revisions_to_merge[add_rev.branch] = add_rev

		# Now add previously merged revisions from add_rev to the merged_revisions dictionary
		for rev_info in add_rev.merged_revisions.values():
			if not self.is_merged_from(rev_info):
				self.set_merged_revision(rev_info)
			continue
		return

	def process_parent_revisions(self, HEAD):
		# Either tree is known, or previous commit was imported from previous refs
		if HEAD.tree:
			self.parents.append(HEAD)

		# Process revisions to merge dictionary, if present
		if self.revisions_to_merge is not None:
			for parent_rev in self.revisions_to_merge.values():
				# Add newly merged revisions to self.merged_revisions dict
				if self.is_merged_from(parent_rev):
					continue

				self.set_merged_revision(parent_rev)

				self.parents.append(parent_rev)
				continue

			self.revisions_to_merge = None

		return

	### Get which revision of the branch of interest have been merged
	def get_merged_revision(self, rev_info_or_branch):
		if type(rev_info_or_branch) is project_branch_rev:
			rev_info_or_branch = rev_info_or_branch.branch

		merged_rev = self.merged_revisions.get(rev_info_or_branch)
		return merged_rev

	def set_merged_revision(self, merged_rev):
		if self.merged_revisions is self.prev_rev.merged_revisions:
			self.merged_revisions = self.prev_rev.merged_revisions.copy()
		self.merged_revisions[merged_rev.branch] = merged_rev
		return

	### Returns True if rev_info_or_branch (if branch, then its HEAD) is one of the ancestors of 'self'.
	# If rev_info_or_branch is a branch, its HEAD is used.
	# If skip_empty_revs is True, then the revision of interest is considered merged
	# even if it's a descendant of the merged revision, but there's been no changes
	# between them
	def is_merged_from(self, rev_info_or_branch, skip_empty_revs=False):
		if type(rev_info_or_branch) is project_branch:
			branch = rev_info_or_branch
			rev_info = branch.HEAD
		else:
			branch = rev_info_or_branch.branch
			rev_info = rev_info_or_branch

		if branch is self.branch:
			# A previous revision of the same sequence of the branch
			# is considered merged
			return True

		merged_rev = self.get_merged_revision(branch)
		if merged_rev is None:
			return False
		if skip_empty_revs:
			rev_info = rev_info.walk_back_empty_revs()

		return merged_rev.rev >= rev_info.rev

	### walk back rev_info if it doesn't have any changes
	# WARNING: it may return a revision with rev = None
	def walk_back_empty_revs(self):
		while self.prev_rev is not None \
				and self.prev_rev.rev is not None \
				and not self.any_changes_present \
				and len(self.parents) < 2:	# not a merge commit
			self = self.prev_rev
		return self

	def add_copy_source(self, source_path, target_path, copy_rev, copy_branch=None):
		if copy_rev is None:
			return

		if copy_branch:
			self.add_branch_to_merge(copy_branch, copy_rev)
		return

	## Adds a parent branch, which will serve as the commit's parent.
	# If multiple revisions from a branch are added as a parent, highest revision is used for a commit
	# the branch also inherits all merged sources from the parent revision
	def add_branch_to_merge(self, source_branch, rev_to_merge):
		if type(rev_to_merge) is int:
			if source_branch is None:
				return

			rev_to_merge = source_branch.get_revision(rev_to_merge)

		if rev_to_merge is None:
			return

		self.add_parent_revision(rev_to_merge)
		return

	def add_tag(self, tag_ref):
		if self.tags is None:
			self.tags = [tag_ref]
		elif tag_ref not in self.tags:
			# If multiple files get same label, apply the label only once
			self.tags.append(tag_ref)
		return

	def get_difflist(self, old_tree, new_tree):
		branch = self.branch
		if old_tree is None:
			old_tree = branch.proj_tree.empty_tree
		if new_tree is None:
			new_tree = branch.proj_tree.empty_tree

		difflist = []
		for t in old_tree.compare(new_tree, "", expand_dir_contents=True):

			difflist.append(t)
			continue

		return difflist

	def build_difflist(self, HEAD):

		return self.get_difflist(HEAD.tree, self.tree)

	def get_stagelist(self, difflist, stagelist):
		branch = self.branch

		for t in difflist:
			path = t[0]
			obj1 = t[1]
			obj2 = t[2]
			item1 = t[3]
			item2 = t[4]

			if obj2 is None:
				# a path is deleted
				if not obj1.is_file():
					continue

				stagelist.append(SimpleNamespace(path=path, obj=None, mode=0))
				continue

			if not obj2.is_file():
				continue

			if item2 is not None and hasattr(item2, 'mode'):
				mode = item2.mode
			else:
				mode = branch.get_file_mode(path, obj2)

			stagelist.append(SimpleNamespace(path=path, obj=obj2, mode=mode))
			continue

		return

	def build_stagelist(self, HEAD):
		difflist = self.build_difflist(HEAD)
		# Parent revs need to be processed before building the stagelist
		self.process_parent_revisions(HEAD)

		branch = self.branch

		stagelist = []
		self.get_stagelist(difflist, stagelist)

		self.git_env = branch.git_env

		for item in stagelist:
			obj = item.obj
			if obj is None:
				continue
			if obj.git_sha1 is not None:
				continue

			if obj.is_symlink():
				path = None
			else:
				path = item.path

			obj.git_sha1 = branch.hash_object(obj.data,
								path, self.git_env)
			continue

		self.staged_tree = self.tree
		self.any_changes_present = len(stagelist) != 0

		return stagelist

	def apply_stagelist(self, stagelist):
		branch = self.branch
		git_repo = branch.git_repo
		git_env = self.git_env

		if stagelist:
			branch.stage_changes(stagelist, git_env)
			return git_repo.write_tree(git_env)
		else:
			return self.prev_rev.staged_git_tree

## project_branch - keeps a context for a single change branch (or tag) of a project
class project_branch:

	def __init__(self, proj_tree:project_history_tree, branch_map, workdir:Path):
		self.name = branch_map.name
		self.proj_tree = proj_tree
		# Matching project's config
		self.cfg:project_config.project_config = branch_map.cfg
		self.git_repo = proj_tree.git_repo

		self.revisions = []
		self.first_revision = None

		self.edit_msg_list = []
		for edit_msg in *branch_map.edit_msg_list, *self.cfg.edit_msg_list:
			if edit_msg.branch.fullmatch(self.name):
				self.edit_msg_list.append(edit_msg)
			continue

		# Absolute path to the working directory.
		# index file (".git.index") will be placed there
		self.git_index_directory = workdir
		if workdir:
			workdir.mkdir(parents=True, exist_ok = True)

		self.git_env = self.make_git_env()

		# Null tree SHA1
		self.initial_git_tree = '4b825dc642cb6eb9a060e54bf8d69288fbee4904'

		# Full ref name for Git branch or tag for this branch
		self.refname = branch_map.refname

		if branch_map.revisions_ref:
			self.revisions_ref = branch_map.revisions_ref
		elif self.refname.startswith('refs/heads/'):
			self.revisions_ref = branch_map.refname.replace('refs/heads/', 'refs/revisions/', 1)
		else:
			self.revisions_ref = branch_map.refname.replace('refs/', 'refs/revisions/', 1)

		self.init_head_rev()

		return

	def init_head_rev(self):
		HEAD = project_branch_rev(self)
		HEAD.staged_git_tree = self.initial_git_tree

		self.HEAD = HEAD
		self.stage = project_branch_rev(self, HEAD)
		return

	## Adds a parent branch, which will serve as the commit's parent.
	# If multiple revisions from a branch are added as a parent, highest revision is used for a commit
	# the branch also inherits all merged sources from the parent revision
	def add_branch_to_merge(self, source_branch, rev_to_merge):
		self.stage.add_branch_to_merge(source_branch, rev_to_merge)
		return

	def add_copy_source(self, copy_path, target_path, copy_rev, copy_branch=None):
		return self.stage.add_copy_source(copy_path, target_path, copy_rev, copy_branch)

	def set_rev_info(self, rev, rev_info):
		# get the head commit
		if not self.revisions:
			self.first_revision = rev
		elif rev < self.first_revision:
			return
		rev -= self.first_revision
		total_revisions = len(self.revisions)
		if rev < total_revisions:
			self.revisions[rev] = rev_info
			return
		if rev > total_revisions:
			self.revisions += self.revisions[-1:] * (rev - total_revisions)
		self.revisions.append(rev_info)
		return

	def get_revision(self, rev=-1):
		if type(rev) is not int:
			# If revision is not present by ID string, history_reader.get_revision raises exception
			rev = self.proj_tree.get_revision(rev).rev
		if rev <= 0 or not self.revisions:
			# get the head commit
			return self.HEAD
		rev -= self.first_revision
		if rev < 0 or not self.revisions:
			return None
		if rev >= len(self.revisions):
			return self.revisions[-1]
		return self.revisions[rev]

	### make_git_env sets up a map with GIT_INDEX_FILE and GIT_WORKING_DIR items,
	# to be used as environment for Git invocations
	def make_git_env(self):
		if self.git_index_directory:
			return self.git_repo.make_env(
					work_dir=str(self.git_index_directory),
					index_file=str(self.git_index_directory.joinpath(".git.index")))
		return {}

	def set_head_revision(self, revision):
		rev_info = self.stage.set_revision(revision)
		if rev_info is None:
			return None
		self.set_rev_info(rev_info.rev, rev_info)
		return rev_info

	def apply_tag(self, tag):
		# Map the branch and label name to a tag
		tag_ref = self.cfg.map_tag(tag)
		# If there's no mapping for a tag, map_tag returns None
		# If a tag is explicitly unmapped, map_tag returns ""
		if tag_ref:
			self.stage.add_tag(tag_ref)
		elif tag_ref is None:
			print('WARNING: Tag "%s" not mapped to any ref' % (tag,), file=self.proj_tree.log_file)
		else:
			print('WARNING: Tag "%s" explicitly not mapped to a ref' % (tag,), file=self.proj_tree.log_file)
		return

	### The function makes a commit on this branch, using the properties from
	# history_revision object to set the commit message, date and author
	# If there is no changes, and this is a tag
	def make_commit(self, revision):
		rev_info = self.set_head_revision(revision)
		if rev_info is None:
			# The branch haven't been re-created after deletion
			# (could have happened on 'replace' command)
			return

		HEAD = self.HEAD
		self.HEAD = rev_info

		git_repo = self.git_repo
		if git_repo is None:
			self.stage = project_branch_rev(self, rev_info)
			return

		stagelist = rev_info.build_stagelist(HEAD)

		rev_info.staged_git_tree = rev_info.apply_stagelist(stagelist)

		# Can only make the next stage rev after done with building the stagelist
		# and processing the parent revision
		self.stage = project_branch_rev(self, rev_info)

		parent_commits = []
		parent_git_tree = self.initial_git_tree
		parent_tree = None
		commit = None

		base_rev = None
		for parent_rev in rev_info.parents:
			if parent_rev.commit is None:
				continue
			if parent_rev.commit not in parent_commits:
				parent_commits.append(parent_rev.commit)
				if base_rev is None or base_rev.committed_git_tree == self.initial_git_tree:
					base_rev = parent_rev

		if base_rev is not None:
			parent_git_tree = base_rev.committed_git_tree
			parent_tree = base_rev.committed_tree
			commit = base_rev.commit

		need_commit = rev_info.staged_git_tree != parent_git_tree
		if len(parent_commits) > 1:
			need_commit = True

		if need_commit:
			rev_props = rev_info.get_commit_revision_props(base_rev)
			author_info = rev_props.author_info

			commit = git_repo.commit_tree(rev_info.staged_git_tree, parent_commits, rev_props.log,
					author_name=author_info.author, author_email=author_info.email, author_date=rev_props.date,
					committer_name=author_info.author, committer_email=author_info.email, committer_date=rev_props.date,
					env=self.git_env)

			print("COMMIT:%s REF:%s BRANCH:%s;%s" % (commit, self.refname, self.name, rev_info.rev), file=rev_info.log_file)

			# Make a ref for this revision in refs/revisions namespace
			if self.revisions_ref:
				self.update_ref('%s/r%s' % (self.revisions_ref, rev_info.rev), commit, log_file=self.proj_tree.revision_ref_log_file)

			rev_info.rev_commit = commit	# commit made on this revision, not inherited
			rev_info.committed_git_tree = rev_info.staged_git_tree
			rev_info.committed_tree = rev_info.tree
			self.proj_tree.commits_made += 1
		else:
			rev_info.committed_git_tree = parent_git_tree
			rev_info.committed_tree = parent_tree

		if rev_info.tags is not None:
			for refname in rev_info.tags:
				if rev_info.props_list and refname.startswith('refs/tags/'):
					props = rev_info.props_list[0]
					if props.log:
						self.create_tag(refname, commit, props, log_file=rev_info.log_file.revision_ref)
						continue
				self.update_ref(refname, commit, log_file=rev_info.log_file.revision_ref)
				continue

		rev_info.commit = commit
		return

	def stage_changes(self, stagelist, git_env):
		git_process = self.git_repo.update_index(git_env)
		pipe = git_process.stdin
		for item in stagelist:
			if item.obj is None:
				# a path is deleted
				pipe.write(b"000000 0000000000000000000000000000000000000000 0\t%s\n" % bytes(item.path, encoding='utf-8'))
				continue
			# a path is created or replaced
			pipe.write(b"%06o %s 0\t%s\n" % (item.mode, bytes(item.obj.get_git_sha1(), encoding='utf-8'), bytes(item.path, encoding='utf-8')))

		pipe.close()
		git_process.wait()

		return

	def get_file_mode(self, path, obj):
		if obj.is_dir():
			return 0o40000

		if obj.is_symlink():
			return 0o120000

		if obj.get_property(b'executable', False):
			return 0o100755

		return 0o100644

	def hash_object(self, data, path, git_env):
		return self.git_repo.hash_object_async(data, path, env=git_env)

	def preprocess_blob_object(self, obj, path):
		proj_tree = self.proj_tree

		if obj.is_symlink():
			return obj

		# Find git attributes - TODO fill cfg.gitattributes
		for attr in self.cfg.gitattributes:
			if attr.pattern.fullmatch(path) and obj.git_attributes.get(attr.key) != attr.value:
				obj = obj.make_unshared()
				obj.git_attributes[attr.key] = attr.value

		obj = proj_tree.finalize_object(obj)
		return obj

	def finalize(self):

		sha1 = self.HEAD.commit
		if not sha1:
			if self.HEAD.tree:
				# Check for refname conflict
				refname = self.cfg.map_ref(self.refname)
				refname = self.proj_tree.make_unique_refname(refname, self.name, self.proj_tree.log_file)
			# else: The branch was deleted
			return

		if self.refname:
			self.update_ref(self.refname, sha1)

		return

	def update_ref(self, refname, sha1, log_file=None):
		refname = self.cfg.map_ref(refname)
		return self.proj_tree.update_ref(refname, sha1, self.name, log_file)

	def create_tag(self, tagname, sha1, props, log_file=None):
		tagname = self.cfg.map_ref(tagname)
		return self.proj_tree.create_tag(tagname, sha1, props, self.name, log_file)

def make_git_object_class(base_type):
	class git_object(base_type):
		def __init__(self, src = None, properties=None):
			super().__init__(src, properties)
			if src:
				self.git_attributes = src.git_attributes.copy()
			else:
				# These attributes also include prettyfication and CRLF normalization attributes:
				self.git_attributes = {}
			return

		# return hashlib SHA1 object filled with hash of prefix, data SHA1, and SHA1 of all attributes
		def make_object_hash(self):
			h = super().make_object_hash()

			# The dictionary provides the list in order of adding items
			# Make sure the properties are hashed in sorted order.
			gitattrs = list(self.git_attributes.items())
			gitattrs.sort()
			for (key, data) in gitattrs:
				h.update(b'ATTR: %s %d\n' % (key.encode(encoding='utf-8'), len(data)))
				h.update(data)

			return h

		def print_diff(obj2, obj1, path, fd):
			super().print_diff(obj1, path, fd)

			if obj1 is None:
				for key in obj2.git_attributes:
					print("  GIT ATTR: %s=%s" % (key, obj2.git_attributes[key]), file=fd)
				return

			# Print changed attributes

			if obj1.git_attributes != obj2.git_attributes:
				for key in obj1.git_attributes:
					if key not in obj2.git_attributes:
						print("  GIT ATTR DELETED: " + key, file=fd)
				for key in obj2.git_attributes:
					if key not in obj1.git_attributes:
						print("  GIT ATTR ADDED: %s=%s" % (key, obj2.git_attributes[key]), file=fd)
				for key in obj1.git_attributes:
					if key in obj2.git_attributes and obj1.git_attributes[key] != obj2.git_attributes[key]:
						print("  GIT ATTR CHANGED: %s=%s" % (key, obj2.git_attributes[key]), file=fd)
			return

	return git_object

class git_tree(make_git_object_class(object_tree)):

	class item:
		def __init__(self, name, obj, mode=None):
			self.name = name
			self.object = obj
			if obj.is_file() and mode:
				self.mode = mode
			return

class git_blob(make_git_object_class(object_blob)):
	def __init__(self, src = None, properties=None):
		super().__init__(src, properties)
		# this is git sha1, produced by git-hash-object, as 40 chars hex string.
		# it's not copied during copy()
		self.git_sha1 = None
		return

	def get_git_sha1(self):
		return str(self.git_sha1)

	def is_symlink(self):
		return self.get_property(b'symlink', None) is not None

class project_history_tree(history_reader):
	BLOB_TYPE = git_blob
	TREE_TYPE = git_tree

	def __init__(self, options=None):
		super().__init__(options)

		self.options = options
		self.log_file = options.log_file
		# This is a tree of branches
		self.head_branch = None
		# class path_tree iterates in the tree recursion order: from root to branches
		# branches_list will iterate in order in which the branches are created
		self.branches_list = []
		# Memory file to write revision ref updates
		self.revision_ref_log_file = io.StringIO()
		# This path tree is used to detect refname collisions, when a new branch
		# is created with an already existing ref
		self.all_refs = path_tree()
		# This is list of project configurations in order of their declaration
		self.project_cfgs_list = project_config.project_config.make_config_list(options.config,
											getattr(options, 'project_filter', []),
											project_config.project_config.make_default_config(options))

		target_repo = getattr(options, 'target_repo', None)
		if target_repo:
			self.git_repo = git_repo.GIT(target_repo)
			# Get absolute path of git-dir
			git_dir = self.git_repo.get_git_dir(True)
			self.git_working_directory = Path(git_dir, "hg_temp")
		else:
			self.git_repo = None
			self.git_working_directory = None

		self.commits_made = 0
		self.branch_dir_index = 1	# Used for branch working directory
		self.total_branches_made = 0
		self.total_tags_made = 0
		self.total_refs_to_update = 0
		self.prev_commits_made = None

		return

	def shutdown(self):
		self.git_repo.shutdown()
		shutil.rmtree(self.git_working_directory, ignore_errors=True)
		self.git_working_directory = None
		return

	def get_parent_revision_tree(self, revision):
		parent_revision = self.get_parent_revision(revision)
		if parent_revision is not None:
			branch = parent_revision.branch
			self.head_branch = branch
			revision.branch = branch
			tree = parent_revision.tree
			if tree is not None:
				return tree
			return self.empty_tree

		self.head_branch = None
		revision.branch = None
		return self.empty_tree

	## Finds an existing branch for the path and revision
	# @param path - the path to find a branch.
	#  The target branch path will be a prefix of path argument
	# @param rev - revision
	# The function is used to find a merge parent.
	# If a revision was not present in a branch, return None.
	def find_branch_rev(self, path, rev):
		# find project, find branch from project
		branch = self.head_branch
		if branch:
			return branch.get_revision(rev)
		return None

	def all_branches(self) -> Iterator[project_branch]:
		return iter(self.branches_list)

	def set_branch_changed(self, branch):
		if branch not in self.branches_changed:
			self.branches_changed.append(branch)
		return

	def get_branch_map(self, name):
		for cfg in self.project_cfgs_list:
			branch_map = cfg.map_branch(name)
			if branch_map is None:
				continue

			if not branch_map.refname:
				# This path is blocked from creating a branch on it
				if branch_map.name == name:
					print('Branch "%s" mapping with globspec "%s" in config "%s":\n'
								% (name, branch_map.globspec, cfg.name),
							'         Blocked from creating a branch',
							file=self.log_file)
				break

			branch_map.cfg = cfg
			return branch_map
		else:
			# See if any parent directory is explicitly unmapped.
			# Note that as directories get added, the parent directory has already been
			# checked for mapping
			print('Branch mapping: No map for "%s" to create a Git branch' % name, file=self.log_file)

		return None

	## Adds a new branch for name in this revision, possibly with source revision
	# The function must not be called when a branch already exists
	def add_branch(self, branch_map):
		print('Branch "%s" mapping with globspec "%s" in config "%s":'
				% (branch_map.name, branch_map.globspec, branch_map.cfg.name),
				file=self.log_file)

		if self.git_working_directory:
			git_workdir = Path(self.git_working_directory, str(self.branch_dir_index))
			self.branch_dir_index += 1
		else:
			git_workdir = None

		branch = project_branch(self, branch_map, git_workdir)
		if branch.refname:
			print('    Added new branch %s' % (branch.refname), file=self.log_file)
		else:
			print('    Added new unnamed branch', file=self.log_file)

		self.branches_list.append(branch)

		return branch

	def make_unique_refname(self, refname, name, log_file):
		if not refname:
			return refname
		new_ref = refname
		# Possible conflicts:
		# a) The terminal path element conflicts with an existing terminal tree element. Can add a number to it
		# b) The terminal path element conflicts with an existing non-terminal tree element (directory). Can add a number to it
		# c) The non-terminal path element conflicts with an existing terminal tree element (leaf). Impossible to resolve

		# For terminal elements, leaf if set to the 
		for i in range(1, 100):
			node = self.all_refs.get_node(new_ref, match_full_path=True)
			if node is None:
				# Full path doesn't match, but partial path may exist
				break
			# Full path matches, try next refname
			new_ref = refname + '___%d' % i
			i += 1
		else:
			print('WARNING: Unable to find a non-conflicting name for "%s",\n'
				  '\tTry to adjust the map configuration' % refname,
				file=log_file)
			return None

		if self.all_refs.find_path(new_ref, match_full_path=False):
			if not self.all_refs.get_used_by(new_ref, key=new_ref, match_full_path=False):
				was_used_by = self.all_refs.get_used_by(new_ref, match_full_path=False)
				self.all_refs.set_used_by(new_ref, new_ref, name, match_full_path=False)
				print('WARNING: Unable to find a non-conflicting name for "%s",\n'
					  '\tbecause the partial path is already a non-directory mapped by "%s".\n'
					  '\tTry to adjust the map configuration'
						% (refname, was_used_by[1]), file=log_file)
				return None
			if name is not None:
				print('WARNING: Refname "%s" is already used by "%s";'
					% (refname, self.all_refs.get_used_by(refname)[1]), file=log_file)
				print('         Remapped to "%s"' % new_ref, file=log_file)

		self.all_refs.set(new_ref, new_ref)
		self.all_refs.set_used_by(new_ref, new_ref, name, match_full_path=True)
		return new_ref

	def update_ref(self, ref, sha1, name, log_file=None):
		if log_file is None:
			log_file = self.log_file

		ref = self.make_unique_refname(ref, name, log_file)
		if not ref or not sha1:
			return ref

		print('WRITE REF: %s %s' % (sha1, ref), file=log_file)

		if ref.startswith('refs/tags/'):
			self.total_tags_made += 1
		elif ref.startswith('refs/heads/'):
			self.total_branches_made += 1

		self.git_repo.queue_update_ref(ref, sha1)
		self.total_refs_to_update += 1

		return ref

	def create_tag(self, tagname, sha1, props, name, log_file=None):
		if log_file is None:
			log_file = self.log_file

		tagname = self.make_unique_refname(tagname, name, log_file)
		if not tagname or not sha1:
			return tagname

		print('CREATE TAG: %s %s' % (sha1, tagname), file=log_file)

		self.git_repo.tag(tagname.removeprefix('refs/tags/'), sha1, props.log,
			props.author_info.author, props.author_info.email, props.date, '-f')
		self.total_tags_made += 1

		return tagname

	# To adjust the new objects under this node with Git attributes,
	# we will override history_reader:make_blob
	def make_blob(self, data, node, properties=None):
		obj = super().make_blob(data, node, properties)
		return self.preprocess_blob_object(obj, node)

	def preprocess_blob_object(self, obj, node):
		if node is None:
			return obj

		branch = self.head_branch
		if branch is None:
			return obj

		# New object has just been created
		return branch.preprocess_blob_object(obj, node.path)

	def copy_blob(self, src_obj, node, properties):
		obj = super().copy_blob(src_obj, node, properties)
		return self.preprocess_blob_object(obj, node)

	def apply_node(self, node, base_tree):

		if node.kind == b'branch':
			return self.apply_branch_node(node, base_tree)

		base_tree = super().apply_node(node, base_tree)

		branch = self.head_branch
		if branch is not None:
			self.set_branch_changed(branch)

		return base_tree

	def apply_file_node(self, node, base_tree):
		base_tree = super().apply_file_node(node, base_tree)
		if node.action != b'delete':
			branch = self.head_branch
			if branch:
				file = base_tree.find_path(node.path)
				base_tree = base_tree.set(node.path, file,
								mode=branch.get_file_mode(node.path, file))
		return base_tree

	def apply_branch_node(self, node, base_tree):
		branch = self.head_branch

		if node.action == b'delete':
			# Find the branch by revision ID
			delete_rev_id = node.path
			if delete_rev_id is not None:
				delete_revision = self.revision_dict.get(delete_rev_id, None)
				if delete_revision is not None:
					if delete_revision.branch is branch:
						self.head_branch = None
						return self.empty_tree
			return base_tree

		if node.action == b'tag':
			if branch is not None:
				branch.apply_tag(node.tag)
				self.set_branch_changed(branch)
			return base_tree

		if node.action == b'cherrypick':
			# This is hg_history_revision:
			try:
				cherry_pick_rev = self.get_revision(node.copyfrom_rev)
				# Get the branch revision project_branch_rev:
				cherry_pick_rev = cherry_pick_rev.branch.get_revision(cherry_pick_rev.rev)
				branch.stage.cherry_pick_revs = [cherry_pick_rev]
				branch.stage.add_dependency(cherry_pick_rev)
			except Exception_history_parse:
				# A cherry-pick source may have been deleted
				print("CHERRY-PICK: Graft source revision %s not found" % (node.copyfrom_rev, ), file=self.log_file)
			return base_tree

		if node.action == b'add':
			# node.path is the branch name to add
			branch_map = self.get_branch_map(node.path)
			if branch_map is None:
				return base_tree

			if self.git_working_directory:
				git_workdir = Path(self.git_working_directory, str(self.branch_dir_index))
				self.branch_dir_index += 1
			else:
				git_workdir = None

			branch = project_branch(self, branch_map, git_workdir)

			if branch.refname:
				print('    Added new branch %s' % (branch.refname), file=self.log_file)
			else:
				print('    Added new unnamed branch', file=self.log_file)

			self.head_branch = branch
			self.branches_list.append(branch)
			self.HEAD().branch = branch

			if node.copyfrom_rev is None:
				return base_tree

		elif node.action != b'parent':
			return base_tree

		copy_source_rev = self.get_revision(node.copyfrom_rev)
		if copy_source_rev is None:
			raise Exception_history_parse('Parent revision %s for branch %s not found' % (node.copyfrom_rev, node.path))
		branch.add_branch_to_merge(copy_source_rev.branch, copy_source_rev.rev)
		if node.action == b'add':
			# If adding the branch, inherit the tree from its parent branch
			base_tree = copy_source_rev.tree
		self.set_branch_changed(branch)
		return base_tree

	def apply_revision(self, revision):
		# Apply the revision to the previous revision, checking if new branches are created
		# into commit(s) in the git repository.

		revision = super().apply_revision(revision)

		# make commits
		for branch in self.branches_changed:
			branch.make_commit(revision)

		self.branches_changed.clear()

		return revision

	def print_progress_line(self, rev=None):

		if rev is None:
			if self.commits_made == self.prev_commits_made:
				return

			self.print_progress_message("Processed %d revisions, made %d commits"
				% (self.total_revisions, self.commits_made), end='\r')

		elif self.commits_made:
			if self.commits_made == self.prev_commits_made:
				return

			self.print_progress_message("Processing revision %s, total %d commits"
				% (rev, self.commits_made), end='\r')
		else:
			return super().print_progress_line(rev)

		self.prev_commits_made = self.commits_made
		return

	def print_last_progress_line(self):
		if not self.commits_made:
			super().print_last_progress_line()
		return

	def print_final_progress_line(self):
		if self.commits_made:
			self.print_progress_message("Processed %d revisions, made %d commits, written %d branches and %d tags in %s"
								% (self.total_revisions, self.commits_made, self.total_branches_made, self.total_tags_made, self.elapsed_time_str()))
		return

	def load(self, revision_reader):
		git_repo = self.git_repo

		self.branches_changed = []

		if not git_repo:
			return super().load(revision_reader)

		# delete it if it existed
		shutil.rmtree(self.git_working_directory, ignore_errors=True)
		# make temp directory
		self.git_working_directory.mkdir(parents=True, exist_ok = True)

		try:
			super().load(revision_reader)

			# Flush the log of revision ref updates
			self.log_file.write(self.revision_ref_log_file.getvalue())

			self.finalize_branches()

			self.print_progress_message(
				"\r                                                                  \r" +
				"Updating %d refs...." % self.total_refs_to_update, end='')

			git_repo.commit_refs_update()

			self.print_progress_message("done")
			self.print_final_progress_line()

		finally:
			self.shutdown()

		return

	def finalize_branches(self):
		for branch in self.branches_list:
			# branch.finalize() writes the refs
			branch.finalize()

		return

def print_stats(fd):
	git_repo.print_stats(fd)
	return
