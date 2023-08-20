#   Copyright 2023 Alexandre Grigoriev
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
import sys
import re
import datetime

from lookup_tree import bytes_path_tree

TOKEN_PIPE          = '|'
TOKEN_QUESTION_MARK = '?'
TOKEN_LEFT_PAREN    = '('
TOKEN_RIGHT_PAREN   = ')'

tokenizer_re = re.compile(rb'(\^|\$|\\.|\\|\.\*|\.|\.\?|\[\^/\]\*|\[|\]|\||\(:|\(|\)|\?|\*|\+|\{|\})')
def tokenize_regexp(regexp:bytes):
	if not regexp:
		return

	prev_end = 0
	for m in tokenizer_re.finditer(regexp):
		start, end = m.span()
		if prev_end != start:
			# Return the string between matches, except for empty string
			yield regexp[prev_end:start], start
		prev_end = end

		token = m[0]
		if token == b'\\.':
			token = b'.'
		elif token == b'.*':
			token = b'**'
		elif token == b'.':
			token = b'?'
		elif token == b'[^/]*':
			token = b'*'
		elif token == b'?':
			token = TOKEN_QUESTION_MARK
		elif token == b'|':
			token = TOKEN_PIPE
		elif token == b'(':
			token = TOKEN_LEFT_PAREN
		elif token == b')':
			token = TOKEN_RIGHT_PAREN
		elif token == b'.?' or token == b'\\' or token == b'(:' \
			or token == b'*' or token == b'?' or token == b'+' \
			or token == b'{' or token == b'}' or token == b'[' or token == b']':
			# Cannot convert to a glob
			raise re.error(b"Unsupported token: '%s' at position %d" % (token, start))
		yield token, start

	if prev_end < len(regexp):
		# Return the remaining string
		yield regexp[prev_end:], prev_end
	return

def process_regexp_tokens(tokens_iter, next_token, next_pos):
	# First level of glob list lists regex sections separated by '|'.
	# Second level contains sections separated by parenthesised subexpressions
	glob_list_list = [[b'']]
	while next_token is not None:
		token = next_token
		pos = next_pos
		if token is TOKEN_RIGHT_PAREN:
			break
		next_token, next_pos = next(tokens_iter, (None, None))
		if token is TOKEN_QUESTION_MARK:
			if glob_list_list[-1][-1]:
				glob_list_list[-1][-1] = [glob_list_list[-1][-1], glob_list_list[-1][-1][:-1]]
				glob_list_list[-1].append(b'')
			elif len(glob_list_list[-1]) >= 2 and type(glob_list_list[-1][-2]) is list:
				if b'' not in glob_list_list[-1][-2]:
					glob_list_list[-1][-2].append(b'')
			else:
				raise re.error(b"Unsupported token: '%s' at position %d" % (token, pos))
			continue
		if token is TOKEN_PIPE:
			glob_list_list.append([b''])
			continue
		elif token is TOKEN_LEFT_PAREN:
			parenthesis_pos = pos
			nested_glob_list, next_token, next_pos = process_regexp_tokens(tokens_iter, next_token, next_pos)
			glob_list_list[-1] += [nested_glob_list, b'']
			if next_token is not TOKEN_RIGHT_PAREN:
				raise re.error(b"Parenthesis '(' at position %d not closed" % (parenthesis_pos))
			next_token, next_pos = next(tokens_iter, (None, None))
			continue
		glob_list_list[-1][-1] += token
		continue

	expanded_glob_list = []
	for glob_list in glob_list_list:
		glob_sublist = [b'']
		for glob_item in glob_list:
			if type(glob_item) is list:
				glob_sublist = [a + b for a in glob_sublist for b in glob_item]
			else:
				glob_sublist = [a + glob_item for a in glob_sublist]
		expanded_glob_list += glob_sublist
	return expanded_glob_list, next_token, next_pos

def regexp_to_glob(regexp:bytes):
	# tokenize the regexp
	# convert tokens into glob specifications
	# Note that a regexp is not rooted and matches any part of path, unless '^' and '$' are used
	tokens_iter = tokenize_regexp(regexp)
	next_token, pos = next(tokens_iter, (None, None))
	raw_glob_list, next_token, pos = process_regexp_tokens(tokens_iter, next_token, pos)
	if next_token is not None:
		raise re.error(b"Unmatched right parenthesis ')' at position %d" % (pos))
	# Process START_OF_LINE and END_OF_LINE markers, drop empty lines
	START_OF_LINE = b'^'
	END_OF_LINE   = b'$'
	glob_list = []
	for raw_glob in raw_glob_list:
		glob = raw_glob
		if glob.startswith(START_OF_LINE):
			glob = b'/' + glob[1:]
		elif not glob.startswith(b'**'):
			glob = b'**' + glob
		if glob.find(START_OF_LINE) >= 0:
			raise re.error(b"Misplaced start of line anchor '^'")
		if glob.endswith(END_OF_LINE):
			glob = glob[:-1]
		elif not glob.endswith(b'**'):
			glob = glob + b'**'
		if glob.find(END_OF_LINE) >= 0:
			raise re.error(b"Misplaced end of line anchor '$'")
		if glob:
			glob_list.append(glob)
	return glob_list

def simplify_gitignore_glob(glob:bytes):
	# Replace trailing /** and /* with a single slash, if the remaining is not a single path component
	if glob.startswith(b'**/**'):
		glob = glob.removeprefix(b'**/')
	m = re.fullmatch(rb'(.*?/)((?:[^/*]|(?<!\*)\*)+)/\*+', glob)
	if m is not None and m[1]:
		if m[1] == b'**/':
			# Replace **/component/** and /* with a single path component
			glob = m.expand(rb'\2/')
		else:
			glob = m.expand(rb'\1\2/')
	# If the glob is a single filename or directory name which begins with **, remove one asterisk
	m = re.fullmatch(rb'\*\*([^/*][^/]*/?)', glob)
	if m is not None:
		glob = m.expand(rb'*\1')
	return glob

re1 = re.compile(rb'((?:[^\\#]|\\.)*)(\s*#.*)?')
re2 = re.compile(rb'(glob|rootglob|re|regexp|include|subinclude):(.*)')
def hgignore_to_gitignore(data:bytes):
	lines = []
	syntax = b'regexp'
	for line in data.splitlines():
		# Separate comment and the remaining line
		m1 = re1.fullmatch(line)
		if m1 is None or not m1[1]:
			lines.append(line)
			continue

		glob = m1[1]
		tail = m1[2]
		if tail is None:
			tail = b''

		if glob.startswith(b'syntax:'):
			syntax = glob[7:].strip()
			continue

		m2 = re2.match(glob)
		if m2 is not None:
			line_syntax = m2[1]
			glob = m2[2]
			if line_syntax == b'include':
				# TODO
				lines.append(b'# ' + line)
				continue
			elif line_syntax == b'subinclude':
				# Mercurial requires subinclude: line in the root .hgignore. Git doesn't require that
				lines.append(b'# ' + line)
				continue
		else:
			line_syntax = syntax

		if line_syntax == b'glob':
			# Check if the glob contains specification Git doesn't handle:
			if re.search(rb'\{|\}', glob):
				lines.append(b'# Unsupported glob specification:\n# ' + line)
				continue
			# If the glob is a single component specification,
			# containing only single asterisks, or starts with **/ or **,
			# it doesn't need massaging, otherwise it needs **/ prepended
			if not re.fullmatch(rb'(?!\*\*|/)(?:(?!\*\*)[^/])*/?', glob):
				glob = b'**/' + glob
		elif line_syntax == b'rootglob':
			if re.fullmatch(rb'(?!\*\*|/)(?:[^/])+/?', glob):
				glob = b'/' + glob
		elif line_syntax == b're' or line_syntax == b'regexp':
			try:
				glob_list = regexp_to_glob(glob)
				lines.append(b'# regexp:' + glob+tail)
				for glob in glob_list:
					glob = simplify_gitignore_glob(glob)
					lines.append(glob)
			except re.error as e:
				# Unable to convert the regular expression to a glob
				lines.append(b'# Unsupported regular expression:\n# %s\n# %s' % (e.msg, line))
			continue
		else:
			lines.append(b'# Unrecognized ignore specification:\n# ' + line)
			continue
		glob = simplify_gitignore_glob(glob)
		# Drop .git/ - always implicitly ignored
		if glob != b'.git/':
			lines.append(glob+tail)
		continue
	return b'\n'.join(lines + [b''])	# Make sure the file ends with \n

def changectx_to_tree(changectx):
	tree = bytes_path_tree()
	if changectx is None:
		return tree

	for filename in changectx:
		tree.set(filename, changectx[filename])

	return tree

class hg_revision_node:
	def __init__(self, action:bytes, kind:bytes, path_or_branch:str|bytes,
			data:bytes=None, copy_from_rev=None, tag=None, props=None):
		self.action = action
		self.kind = kind
		if type(path_or_branch) is bytes:
			path_or_branch = path_or_branch.decode()

		self.path = path_or_branch
		self.tag = tag
		self.props = props
		self.copyfrom_path = None
		self.copyfrom_rev = copy_from_rev
		self.text_content = data
		return

	def print(self, fd):
		print("   NODE %s %s:%s%s" % (self.action.decode(),
					self.kind.decode() if self.kind is not None else None, self.path,
					"" if self.action != b'tag' else (', tag: %s' % self.tag)), file=fd)
		if self.copyfrom_rev is not None:
			print("       COPY FROM: %s" % (self.copyfrom_rev), file=fd)
		return

class hg_changectx_revision:
	def __init__(self, reader:hg_repository_reader, changectx, options):
		self.rev = changectx.rev()
		self.changectx_node = changectx.node()
		rev_hex = changectx.hex()
		self.rev_id = rev_hex.decode()
		self.author:str = changectx.user().decode()
		self.log:str = changectx.description().decode()
		date = changectx.date()
		tz = datetime.timezone(datetime.timedelta(seconds=date[1]))
		self.datetime = datetime.datetime.fromtimestamp(date[0], tz=tz)
		self.branch = changectx.branch().decode()
		self.parent_revision = None	# direct ancestor
		self.child_revision = None	# direct descendant
		self.nodes = []
		self.convert_hgignore = options.convert_hgignore

		self.children = [child.hex() for child in changectx.children()]
		parents = []
		for parent_changectx in changectx.parents():
			if parent_changectx.node() != b'\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00':
				parents.append(reader.changectx_dict[parent_changectx.node()])
			continue

		if parents:
			parent_changectx = changectx.parents()[0]
			parent_revision = parents.pop(0)
			if parent_revision.branch != self.branch:
				self.add_revision_node(b'add', b'branch', self.branch, copy_from_rev=parent_revision.rev_id)
				parent_revision.children.remove(rev_hex)
			# check if it creates a new sub-branch (splitting a branch with two with same name)
			elif parent_revision.child_revision is None:
				# Continue the first parent branch
				self.parent_revision = parent_revision
				parent_revision.child_revision = self
				if len(parent_revision.children) == 1:
					# Discard old tree to avoid memory usage ballooning
					parent_revision.tree = None
			else:
				self.add_revision_node(b'add', b'branch', self.branch, copy_from_rev=parent_revision.rev_id)
				parent_revision.children.remove(rev_hex)
		else:
			self.add_revision_node(b'add', b'branch', self.branch)
			parent_changectx = None

		if len(parents) == 0:
			self.process_file_list(changectx, parent_changectx)
		else:
			# We cannot use changectx.files() for diffs. We have to compare trees
			# build tree from all files
			parent_changectx = reader.repository[parent_revision.changectx_node]

			for parent_revision in parents:
				self.add_revision_node(b'parent', b'branch', None, copy_from_rev=parent_revision.rev_id)
				parent_revision.children.remove(rev_hex)
				# Check if this merge ends a sub-branch
				if len(parent_revision.children) == 0:
					self.add_revision_node(b'delete', b'branch', parent_revision.rev_id)
				continue
			self.compare_change_contexts(parent_changectx, changectx)

		for tag in changectx.tags():
			self.create_tag(tag.decode())

		self.extra = changectx.extra().copy()
		self.extra.pop(b'branch', None)

		if cherry_picked_from := self.extra.pop(b'source', None):
			self.add_revision_node(b'cherrypick', b'branch', None, copy_from_rev=cherry_picked_from.decode())
		return

	### Build a changelist for a merge (files() method cannot be used)
	def compare_change_contexts(self, ctx1, ctx2):
		tree1 = changectx_to_tree(ctx1)
		tree2 = changectx_to_tree(ctx2)

		for path, node1, node2 in type(tree1).compare(tree1, tree2):

			if path and path.startswith(b'/'):
				path = path[1:]

			if node1 is None:
				# Node2 added
				if node2.object is not None:
					self.create_file(path, node2.object)
				continue

			if node2 is None:
				# Node1 deleted
				if node1.object is not None:
					self.delete_file(path, ctx2, ctx1)
				# else: empty directories are deleted in the tree
				continue

			# Make sure to correctly handle change from a file to a directory, and the other way around.
			# Note that Mercurial doesn't keep directories.
			if node2.object is None:
				if node1.object is not None:
					self.delete_file(path, ctx2, ctx1)
			elif node1.object is None:
				self.create_file(path, node2.object)
			elif node1.object.filenode() != node2.object.filenode():
				self.change_file(path, node2.object)
			continue
		return

	def process_file_list(self, changectx, parent_changectx):
		for path in changectx.files():
			if path in changectx and \
				(fctx := changectx.filectx(path)):
				if parent_changectx is not None and path in parent_changectx:
					self.change_file(path, fctx)
				else:
					self.create_file(path, fctx)
			else:
				self.delete_file(path, changectx, parent_changectx)
		return

	def add_revision_node(self, action:bytes, kind:bytes, path:str|bytes,
				data:bytes=None, copy_from_rev=None, tag=None, props=None):

		self.nodes.append(hg_revision_node(action, kind, path,
					data=data, copy_from_rev=copy_from_rev, tag=tag, props=props))
		return

	def create_tag(self, tag:str):
		return self.add_revision_node(b'tag', b'branch', None, tag=tag)

	# If both present, .gitignore comes in the diff list and in the file list before .hgignore
	# If both get changed. .gitignore gets changed first, then the generated .gitignore overrides it
	def change_file(self, path:bytes, fctx, action=b'change'):
		data = fctx.data()
		props = {}
		if fctx.islink():
			props[b'symlink'] = b'symlink'
		elif self.convert_hgignore and (path == b'.hgignore' or path.endswith(b'/.hgignore')):
			path = path.removesuffix(b'.hgignore') + b'.gitignore'
			data = hgignore_to_gitignore(data)
			action = b'change'
		elif fctx.isexec():
			props[b'executable'] = b'executable'
		self.add_revision_node(action, b'file', path, data=data, props=props)
		return

	def create_file(self, path:bytes, fctx):
		return self.change_file(path, fctx, action=b'add')

	# If both present, .gitignore comes in the diff list and in the file list before .hgignore
	# If both get deleted. .gitignore gets deleted first. In this case, we should skip deleting the generated file
	def delete_file(self, path:bytes, changectx, parent_changectx):
		if not self.convert_hgignore:
			pass
		elif path == b'.hgignore' or path.endswith(b'/.hgignore'):
			path = path.removesuffix(b'.hgignore') + b'.gitignore'
			# If .gitignore exists at the parent tree, restore it back instead of deleting
			if path in changectx:
				# .gitignore present in the current tree, restore it
				return self.change_file(path, changectx[path])
			elif path in parent_changectx:
				# .gitignore not present in the current tree, but was present in the parent tree and has been already deleted
				return
		elif path == b'.gitignore' or path.endswith(b'/.gitignore'):
			# deleting .gitignore
			hgignore_path = path.removesuffix(b'.gitignore') + b'.hgignore'
			# If .hgignore exists at the current tree, restore .gitignore back instead of deleting
			if hgignore_path in changectx:
				data = hgignore_to_gitignore(changectx[hgignore_path].data())
				return self.add_revision_node(b'change', b'file', path, data=data)

		self.add_revision_node(b'delete', None, path)
		return

	def print(self, fd=sys.stdout):
		print("REVISION: %d (%s), branch: %s, time: %s, author: %s" % (self.rev,
					self.rev_id, self.branch, str(self.datetime), self.author), file=fd)

		if self.log:
			print("MESSAGE: %s" % ("\n         ".join(self.log.splitlines())), file=fd)

		if self.extra:
			print("EXTRA:", file=fd)
			for key, data in self.extra.items():
				try:
					data = data.decode()
				except UnicodeDecodeError:
					data = str(data)
				print("    %s=%s" % (key.decode(), data), file=fd)

		for node in self.nodes:
			node.print(fd)

		print("", file=fd)
		return

class hg_repository_reader:
	def __init__(self, repository_directory:str):

		from mercurial.localrepo import instance as hg_repository
		from mercurial import ui
		self.repository = hg_repository(ui.ui(), repository_directory.encode(), False)

		# Revisions by changectx node()
		self.changectx_dict = {}
		return

	def read_revisions(self, options):
		rev = 0
		pending_changectx_dict = {}
		changes_array = [None] * len(self.repository.changelog)
		# The mercurial repository seems to use a caching scheme with weak references
		# We will keep references to the parents to make sure they will be reused
		for change_idx in range(len(changes_array)):
			t = changes_array[change_idx]
			if t is None:
				# This revision begins an orphan branch
				changectx = self.repository[change_idx]
				parents = []
			else:
				changectx = t[0]
				# Drop the reference to the changeset in the array.
				# If it stays alive, memory commit size balloons greatly
				# and the program can run out of memory on a large repository
				changes_array[change_idx] = None
			revision = hg_changectx_revision(self, changectx, options)
			self.changectx_dict[changectx.node()] = revision

			yield revision
			rev += 1

			# Process the child revisions
			for child_ctx in changectx.children():
				node= child_ctx.node()
				if node in pending_changectx_dict:
					child_ctx, parents, pending_parents = pending_changectx_dict[node]
					# The mercurial repository returns same cached object when requested by node or rev
					pending_parents.remove(changectx)
					if not pending_parents:
						pending_changectx_dict.pop(node)
				else:
					parents = [*child_ctx.parents()]
					pending_parents = parents.copy()
					pending_parents.remove(changectx)

				if pending_parents:
					pending_changectx_dict[node] = (child_ctx, parents, pending_parents)
					continue
				# Also keep references to its parents, to keep them cached
				changes_array[child_ctx.rev()] = (child_ctx, parents)
			continue

		return

def print_stats(fd):
	return
