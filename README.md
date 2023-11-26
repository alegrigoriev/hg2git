# hg-to-git: Mercurial repository reader and convertor to Git

This Python program allows you to read and analyze a Mercurial (HG) repository,
and also optionally convert it to Git repository.

Running the program
-------------------

The program is invoked by the following command line:

`python hg-to-git.py <repository path> [<options>]`

The following command line options are supported:

`--version`
- show program version.

`--config <XML config file>` (or `-c <XML config file>`)
- specify the configuration file for conversion options.
See [XML configuration file](#xml-config-file) chapter.

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

`--branches <branches namespace>`
- use this directory name as the root directory for branches. The default is **refs/heads/**.
This value is also assigned to **$Branches** variable to use for substitutions in the XML config file.

`--tags <tags namespace>`
- use this directory name as the root directory for tags. The default is **refs/tags/**.
This value is also assigned to **$Tags** variable to use for substitutions in the XML config file.

`--no-default-config`
- don't use default namespaces for branches and tags. This option doesn't affect default variable assignments.

`--verbose={dump|revs|all|dump_all}`
- dump additional information to the log file.

	`--verbose=dump`
	- dump revisions to the log file.

	`--verbose=revs`
	- log the difference from each previous revision, in form of added, deleted and modified files and attributes.
This doesn't include file diffs.
	`--verbose=dump_all`
	- dump all revisions, even empty revisions without any change operations.
By default, `--verbose=dump` and `--verbose=all` don't dump empty revisions.

	`--verbose=all`
	- same as `--verbose=dump --verbose=revs`

`--project <project name filter>`
- selects projects to process. This option can appear multiple times. See [Project filtering](#project-filtering).

`--target-repository <target Git repository path>`
- Specifies path to the target Git repository.
The repository should be previously initialized by a proper `git init` command.
The program will not delete existing refs, only override them as needed.

`--decorate-commit-message <tagline type>`
- tells the program to add a tagline to each commit message, depending on `<tagline type>`.
At this time, the only `<tagline type>` supported is `revision-id`,
which tells the program to add `HG-revision: <rev>` taglines with Mercurial revision number to each commit.
By default, the commit messages are undecorated.

XML configuration file{#xml-config-file}
======================

Mapping of Mercurial branches Git branches, and other global and per-branch settings,
is described by an XML configuration file.
This file is specified by `--config` command line option.

The file consists of the root node `<Projects>`, which contains a single section `<Default>` and a number of sub-sections `<Project>`:

```xml
<?xml version="1.0" encoding="utf-8"?>
<Projects xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation=". hg-config.xsd">
	<Default>
		<!-- default settings go here -->
	</Default>
	<Project Name="*" Branch="*">
		<!-- per-project settings go here -->
	</Project>
</Projects>
```

`xsi:schemaLocation` attribute refers to `hg-config.xsd.xsd` schema file provided with this repository,
which can be used to validate the XML file in some editors, for example, in Microsoft Visual Studio.

Wildcard (glob) specifications in the config file{#config-file-wildcard}
-------------------------------------------------

Paths and other path-like values in the configuration file can contain wildcard (glob) characters.
In general, these wildcards follow Unix/Git conventions. The following wildcards are recognized:

'`?`' - matches any character;

'`*`' - matches a sequence of any characters, except for slash '`/`'. The matched sequence can be empty.

'`/*/`' - matches a non-empty sequence of any (except for slash '`/`') characters between two slashes.

'`*/`' in the beginning of a path - matches a non-empty sequence of any (except for slash '`/`') characters before the first slash.

'`**`' - matches a sequence of any characters, _including_ slashes '`/`', **or** an empty string.

'`**/`' - matches a sequence of any characters, _including_ slashes '`/`', ending with a slash '`/`', **or** an empty string.

`{<match1>,...}` - matches one of the comma-separated patterns (each of those patterns can also contain wildcards).

Note that `[range...]` character range Unix glob specification is not supported.

As in Git, a glob specification which matches a single path component (with or without a trailing slash) matches such a component at any position in the path.
If a trailing slash is present, only directory-like components can match.
If there's no trailing slash, both directory- and file-like components can match the given glob specification. Thus, a single '`*`' wildcard matches any filename.
If a glob specification can match multiple path components, it's assumed it begins with a slash '`/`', meaning the match starts with the beginning of the path.

In many places, multiple wildcard specifications can be present, separated by a semicolon '`;`'.
They are tested one by one, until one matches.
In such a sequence, a negative wildcard can be present, prefixed with a bang character '`!`'.
If a negative wildcard matches, the whole sequence is considered no-match.
You can use such negative wildcards to carve exceptions from a wider wildcard.
If all present wildcards are negative, and none of them matches, this considered a positive match, as if there was a "`**`" match all specification in the end.

Variable substitutions in the config file{#variable-substitutions}
-----------------------------------------

You can assign a value to a variable, and have that value substituted whenever a string contains a reference to that variable.

The assignment is done by `<Vars>` section, which can appear under `<Default>` and `<Project>` sections. It has the following format:

```
		<Vars>
			<variable_name>value</variable_name>
		</Vars>
```

The following default variables are preset:

```xml
		<Vars>
			<Branches>refs/heads/</Branches>
			<Tags>refs/tags/</Tags>
		</Vars>
```

They can be overridden explicitly in `<Default>` and `<Project>` sections,
and/or by the command line options `--branches` and `--tags`.

For the variable substitution purposes, the sections are processed in order,
except for the specifications injected from `<Default>` section into `<Project>`.
All `<Vars>` definitions from `<Default>` are processed before all sections in `<Project>`.

For substitution, you refer to a variable as `$<variable name>`,
for example `$Trunk`, or `${<variable name>}`, for example `${Branches}`.
Another acceptable form is `$(<variable name>)`, for example `$(UserBranches)`.
You have to use the form with braces or parentheses
when you need to follow it with an alphabetical character, such as `${MapTrunkTo}1`.

Note that if a variable value is a list of semicolon-separated strings, like `users/branches;branches/users`,
its substitution will match one of those strings,
as if they were in a `{}` wildcard, like `{users/branches,branches/users}`.

A variable definition can refer to other variables. Circular substitutions are not allowed.

The variable substitution is done when the XML config sections are read.
When another `<Vars>` section is encountered, it affects the sections that follow it.

Ref character substitution{#ref-character-substitution}
--------------------------

Certain characters are not allowed in Git refnames.
The program allows to map invalid characters to allowed ones. The remapping is specified by `<Replace>` specification:

```xml
		<Replace>
			<Chars>source character</Chars>
			<With>replace with character</With>
		</Replace>
```

This specification is allowed in `<Default>` and `<Project>` sections.
All `<Replace>` definitions from `<Default>` are processed before all sections in `<Project>`.

Example:

```xml
		<Replace>
			<Chars> </Chars>
			<With>_</With>
		</Replace>
```

This will replace spaces with underscores.

`<Default>` section{#default-section}
---------------

A configuration file can contain zero or one `<Default>` section under the root node.
This section contains mappings and variable definitions to be used as defaults for all projects.
In absence of `<Project>` sections, the `<Default>` section is used as a default project.

`<Default>` section is merged into beginning of each `<Project>` section,
except for `<MapBranch>` specifications,
which are merged _after_ the end of each `<Project>` section.

`InheritDefault="No"` attribute in the `<Default>` section header suppresses
inheritance from the hardcoded configuration.

`InheritDefaultMappings="No"` suppresses inheritance of default `<MapBranch>`
mappings.

`<Vars>` and `<Replace>` specifications are always inherited from the hardcoded defaults
or passed from the command line.

`<Project>` section{#project-section}
---------------

A configuration file can contain zero or more `<Project>` sections under the root node.
This section isolates mappings, variable definitions, and other setting to be used together.

A `<Project>` section can have optional `Name` and `Branch` attributes.

If supplied, `Name` attribute values must be unique: two `<Project>` sections cannot have same name.

The `Branch` value filters the branches to be processed with this `<Project>`.
Its value can be one or more wildcards (glob) specifications, separated by semicolons.

`InheritDefault="No"` attribute in a `<Project>` or `<Default>` section suppresses
inheritance from its default (from hardcoded config or from `<Default>`
section).

`InheritDefaultMappings="No"` suppresses inheritance of default `<MapBranch>`
mappings.

`<Vars>` and `<Replace>` specifications are always inherited from the hardcoded defaults
or passed from the command line. Only their overrides in `<Default>` section will get ignored.

`<Project>` sections with `ExplicitOnly="Yes"` attribute are only used if explicitly selected
by `--project` command line option.

If a `<Project>` section relies on another project section,
for example, it merges paths from another project, specify such requirement with
`NeedsProjects="comma,separated,list"` attribute.

Branch to Ref mapping{#branch-mapping}
-------------------

Unlike Git, Mercurial branches don't live in a `refs/heads/` namespace.
Multiple history lines for one branch can be present and active in the repository.
Thus, the program needs to be told how to map directories to Git refs.

This program provides a default mapping of a branch name to a ref, by prepending `refs/heads/` to a branch name.

Non-default mapping allows to handle more complex cases.

You can map a branch name matching the specified pattern, into a specific Git ref,
built by substitution from the original name. This is done by `<MapBranch>` sections in `<Project>` or `<Default>` sections:

```xml
	<Project>
		<MapBranch>
			<Branch>branch matching specification</Branch>
			<Refname>ref substitution string</Refname>
			<!-- optional: -->
			<RevisionRef>revision ref substitution</RevisionRef>
		</MapBranch>
	</Project>
```

Here, `branch matching specification` is a glob (wildcard) match specification to match the Mercurial branch name,
`<Refname>` is the refname substitution specification to make Git branch refname for this branch,
and the optional `<RevisionRef>` substitution specification makes a root for revision refs for commits made on this branch.

The program replaces special variables and specifications in `ref substitution string`
with strings matching the wildcard specifications in `branch matching specification`.
During the pattern match, each explicit wildcard specification, such as '`?`', '`*`', '`**`', '`{pattern...}`',
assigns a value to a numbered variable `$1`, `$2`, etc.
The substitution string can refer to those variables as `$1`, `$2`, or as `${1}`, `$(2)`, etc.
Explicit brackets or parentheses are required if the variable occurrence has to be followed by a digit.
If the substitutions are in the same order as original wildcards, you can also refer to them as '`*`', '`**`'.

Note that you can only refer to wildcards in the initial match specification string,
not to wildcards inserted to the match specification through variable substitution.

Every time a new branch is created in a repository,
the program tries to map its name into a symbolic reference AKA ref.

`<MapBranch>` definitions are processed in their order in the config file in each `<Project>`.
First `<Project>` definitions are processed, then definitions from `<Default>`,
and then default mappings described above (unless they are suppressed by `--no-default-config` command line option).

The first `<MapBranch>` with `<Branch>` matching the branch name will define which Git "branch" this directory belongs to.

The target refname in `<Refname>` specification is assumed to begin with `refs/` prefix.
If the `refs/` prefix is not present, it's implicitly added.

If a refname produced for a branch collides with a refname for a different branch,
the program will try to create an unique name by appending `__<number>` to it.

If `<Refname>` specification is omitted, this name is explicitly unmapped from creating a branch.

The program can create a special ref for each commit it makes, to map Mercurial commits to Git commits.
An optional `<RevisionRef>` specification defines how the revision ref name root is formatted.
Without `<RevisionRef>` specification, an implicit mapping will make
refnames for branches (Git ref matching `refs/heads/<branch name>`) as `refs/revisions/<branch name>/r<rev number>`.

Tag to Ref mapping{#tag-mapping}
-------------------

Unlike Git, Mercurial tags don't live in a `refs/tags/` namespace. They are tracked with `.hgtags` file.
Thus, the program needs to be told how to map tags to Git refs.

This program provides a default mapping of a tag name to a ref, by prepending `refs/tags/` to a tag name.

Non-default mapping allows to handle more complex cases.

You can map a tag name matching the specified pattern, into a specific Git ref,
built by substitution from the original name. This is done by `<MapTag>` sections in `<Project>` or `<Default>` sections:

```xml
	<Project>
		<MapTag>
			<Tag>tag matching specification</Tag>
			<Refname>ref substitution string</Refname>
		</MapTag>
	</Project>
```

Here, `tag matching specification` is a glob (wildcard) match specification to match the Mercurial branch name,
`<Refname>` is the refname substitution specification to make Git branch refname for this branch,
and the optional `<RevisionRef>` substitution specification makes a root for revision refs for commits made on this branch.

Every time a new branch is created in a repository,
the program tries to map its path into a symbolic reference AKA ref.

`<MapTag>` definitions are processed in their order in the config file in each `<Project>`.
First `<Project>` definitions are processed, then definitions from `<Default>`,
and then default mappings described above (unless they are suppressed by `--no-default-config` command line option).

The first `<MapTag>` with `<Branch>` matching the tag name will define which Git "branch" this directory belongs to.
The rest of the path will be a subdirectory in the branch worktree.

The target refname in `<Refname>` specification is assumed to begin with `refs/` prefix.
If the `refs/` prefix is not present, it's implicitly added.

If a refname produced for a tag collides with a refname for a different tag,
the program will try to create an unique name by appending `__<number>` to it.

If `<Refname>` specification is omitted, this directory and all its subdirectories are explicitly unmapped from creating a branch. 

Project filtering{#project-filtering}
-----------------

You can select to process only some projects - enable only selected `<Project>` sections in the XML configuration file.
Projects to process are selected by specifying `--project <project name filter>` option(s) in the command line.

Multiple `--project` options can be supplied in the command line.
The option value can contain multiple project name filters, separated by commas.

If a filter is prefixed with an exclamation mark '`!`',
this pattern excludes projects (negative match).
Note that in *bash* command line, '`!`' character needs to be single-quoted as "`'!'`"
to prevent history expansion:

`--project '!'<excluded project pattern>`

Handling of empty commit messages
---------------------------------

Mercurial allows empty commit messages.

The program will generate a commit message describing all added, deleted, changed, renamed files.

Mercurial history tracking{#hg-history-tracking}
----------------

The program makes a new Git commit on a branch when there are changes in its directory tree.
The commit message, timestamps and author/committer are taken from the commit information.
Mercurial doesn't have a distinction between author and committer.

Mapping Mercurial usernames{#Mapping-HG-usernames}
---------------------

Mercurial commits can store short usernames for commit authors, or name+email strings.
Git stores full names and emails.

If a Mercurial username cannot be split into an username and email,
the program will make an email as `<HG username>@localhost`,
same as Git does when `user.email` setting is not configured.

In a Mercurial repository, changesets (commits) may come with author name and email
combined into the changeset username, and even put in quotes sometimes.
The program parses the usernames, making an effort to separate the name and email.
