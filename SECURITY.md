# Security policy

## Supported versions

Security fixes go into the latest release. Please update before reporting a
problem.

## Reporting a vulnerability

Please do not open a public issue. Report it privately through GitHub's
[private vulnerability reporting](https://github.com/dannyking/herdr-sidebar-customizer/security/advisories/new)
(the **Report a vulnerability** button on the repository's **Security** tab).

Include the plugin and Herdr versions, your operating system, and steps to
reproduce. Use synthetic data: do not send real session transcripts,
configuration files or credentials.

This is a volunteer project. We aim to acknowledge reports within a week, keep
you informed while a fix is prepared, and credit you in the advisory unless you
prefer otherwise.

## Scope

The plugin runs with your user's permissions inside Herdr. Reports are
especially welcome about:

- Editing Herdr's `config.toml`, the restoration record, or Restore removing or
  changing content it does not own.
- The optional Claude status-line hook command written to Claude's
  `settings.json`.
- Reading local agent session files and OpenCode's database, including paths
  that escape the allowed session directories.
- Files in the plugin's state directory, and communication with Herdr's socket
  or named pipe.

Problems in Herdr itself or in the agents it runs should be reported to those
projects.
