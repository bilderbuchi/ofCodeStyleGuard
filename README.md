# ofCodeStyleGuard

ofCodeStyleGuard continually checks openFrameworks commits for code style compliance.
It listens for notifications from Github webhooks which indicate fresh PR activity, and checks the resulting PR, presenting a `.diff` or `.patch` file for the PR submitter to run to achieve code style compliance.

## Requirements
* Python 2.6
* [PyGithub](https://github.com/jacquev6/PyGithub)
* git
* Whatever your style script uses

## License
The code in this repository is available under the MIT License (see license.md).

Copyright (c) 2013- Christoph Buchner
