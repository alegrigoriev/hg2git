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
import datetime

from lookup_tree import bytes_path_tree

def changectx_to_tree(changectx):
	tree = bytes_path_tree()
	if changectx is None:
		return tree

	for filename in changectx:
		tree.set(filename, changectx[filename])

	return tree

class hg_revision_node:
	def __init__(self, action:bytes, kind:bytes, path_or_branch:str|bytes, data:bytes=None, copy_from_rev=None):
		self.action = action
		self.kind = kind
		if type(path_or_branch) is bytes:
			path_or_branch = path_or_branch.decode()

		self.path = path_or_branch
		self.props = None
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
	def __init__(self, reader:hg_repository_reader, changectx):
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

		self.children = [child.hex() for child in changectx.children()]
		parents = []
		for parent_changectx in changectx.parents():
			if parent_changectx.node() != b'\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00':
				parents.append(reader.changectx_dict[parent_changectx.node()])
			continue

		if parents:
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

		self.extra = changectx.extra().copy()
		self.extra.pop(b'branch', None)

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
					self.delete_file(path)
				# else: empty directories are deleted in the tree
				continue

			# Make sure to correctly handle change from a file to a directory, and the other way around.
			# Note that Mercurial doesn't keep directories.
			if node2.object is None:
				if node1.object is not None:
					self.delete_file(path)
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
				self.delete_file(path)
		return

	def add_revision_node(self, action:bytes, kind:bytes, path:str|bytes,
				data:bytes=None, copy_from_rev=None):

		self.nodes.append(hg_revision_node(action, kind, path,
					data=data, copy_from_rev=copy_from_rev))
		return

	def change_file(self, path:bytes, fctx, action=b'change'):
		data = fctx.data()
		self.add_revision_node(action, b'file', path, data=data)
		return

	def create_file(self, path:bytes, fctx):
		return self.change_file(path, fctx, action=b'add')

	def delete_file(self, path:bytes):
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
			revision = hg_changectx_revision(self, changectx)
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
