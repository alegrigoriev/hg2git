# parse-hg-repo: Mercurial repository reader

This Python program allows you to read and analyze a Mercurial (HG) repository.

Running the program
-------------------

The program is invoked by the following command line:

`python parse-hg-repo.py <repository path> [<options>]`

The following command line options are supported:

`--version`
- show program version.

`--log <log file>`
- write log to a file. By default, the log is sent to the standard output.

`--verbose[=dump]`
- dump revisions to the log file.
