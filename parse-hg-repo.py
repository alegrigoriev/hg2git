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
	in_repository = sys.argv[1]

	from hg_reader import hg_repository_reader, print_stats as print_hg_stats
	from history_reader import load_history

	try:
		load_history(hg_repository_reader(in_repository), sys.stdout)

	finally:
		print_hg_stats(sys.stdout)

	return 0

from mercurial.error import RepoError
if __name__ == "__main__":
	try:
		sys.exit(main())
	except RepoError as e:
		print("ERROR: Mercurial: %s" % (b''.join(e.args).decode()), file=sys.stderr)
		sys.exit(2)
	except FileNotFoundError as fnf:
		print("ERROR: %s: %s" % (fnf.strerror, fnf.filename), file=sys.stderr)
		sys.exit(1)
	except KeyboardInterrupt:
		# silent abort
		sys.exit(130)
