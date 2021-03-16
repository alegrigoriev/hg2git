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

`--end-revision <REV>`
- makes the dump stop after the specified revision number.

`--quiet`
- suppress progress indication (number of revisions processed, time elapsed).
By default, the progress indication is active on a console,
but is suppressed if the standard error output is not recognized as console.
If you don't want progress indication on the console, specify `--quiet` command line option.

`--progress[=<period>]`
- force progress indication, even if the standard error output is not recognized as console,
and optionally set the update period in seconds as a floating point number.
For example, `--progress=0.1` sets the progress update period 100 ms.
The default update period is 1 second.

`--verbose={dump|revs|all}`
- dump additional information to the log file.

	`--verbose=dump`
	- dump revisions to the log file.

	`--verbose=revs`
	- log the difference from each previous revision, in form of added, deleted and modified files and attributes.
This doesn't include file diffs.

`--verbose=all` is same as `--verbose=dump --verbose=revs`
