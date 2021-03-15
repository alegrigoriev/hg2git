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

if sys.version_info < (3, 9):
	sys.exit("parse-hg-repo: This package requires Python 3.9+")

import os
# By default, Mercurial API returns strings as transcoded from Unicode to local MBCS.
# That's quite inconvenient. Force Mercurial to operate in UTF-8:
os.environ["HGENCODING"] = "UTF-8"

def main():
	import argparse
	parser = argparse.ArgumentParser(description="Parse Mercurial repository", allow_abbrev=False)
	parser.add_argument('--version', action='version', version='%(prog)s 0.1')
	parser.add_argument(dest='in_repository', help="Mercurial repository root directory")
	parser.add_argument("--log", dest='log_file', help="Logfile destination; default to stdout")
	parser.add_argument("--verbose", "-v", dest='verbose', help="Log verbosity:", choices=['dump', 'dump_all', 'revs', 'all'],
						action='append', nargs='?', const=['dump'], default=[])
	parser.add_argument("--end-revision", "-e", metavar='REV', dest='end_revision', help="Revision to stop the input file processing")
	group = parser.add_argument_group()
	group.add_argument("--quiet", '-q', help="Suppress progress indication", action='store_true')
	group.add_argument("--progress", nargs='?', help="Forces progress indication when not detected as on terminal, and optionally sets the update period in seconds",
					type=float, action='store', const='1.', default='1.' if sys.stderr.isatty() else None)

	options = parser.parse_args();

	if options.log_file:
		options.log_file = open(options.log_file, 'wt', 0x100000, encoding='utf=8')
	else:
		options.log_file = sys.stdout
	log_file = options.log_file

	# If -v specified without value, the const list value is assigned as a list item. Extract it to be the part of list instead
	if options.verbose and type(options.verbose[0]) is list:
		o = options.verbose.pop(0)
		options.verbose += o

	options.log_dump = 'dump' in options.verbose or 'all' in options.verbose
	# dump_all is not included in --verbose=all
	options.log_dump_all = 'dump_all' in options.verbose
	options.log_revs = 'revs' in options.verbose or 'all' in options.verbose

	from hg_reader import hg_repository_reader, print_stats as print_hg_stats
	from project_tree import project_history_tree

	project_tree = project_history_tree(options)

	try:
		project_tree.load(hg_repository_reader(options.in_repository))

	finally:
		print_hg_stats(log_file)
		log_file.close()

	return 0

from mercurial.error import RepoError
from exceptions import Exception_history_parse
if __name__ == "__main__":
	try:
		sys.exit(main())
	except RepoError as e:
		print("ERROR: Mercurial: %s" % (b''.join(e.args).decode()), file=sys.stderr)
		sys.exit(2)
	except FileNotFoundError as fnf:
		print("ERROR: %s: %s" % (fnf.strerror, fnf.filename), file=sys.stderr)
		sys.exit(1)
	except Exception_history_parse as ex:
		print("ERROR: %s" % ex.strerror, file=sys.stderr)
		sys.exit(128)
	except KeyboardInterrupt:
		# silent abort
		sys.exit(130)
