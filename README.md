# ofCodeStyleGuard

ofCodeStyleGuard continually checks openFrameworks commits for code style compliance.
It listens for notifications from Github webhooks which indicate fresh PR activity, and checks the resulting PR, presenting a `.patch` file for the PR submitter to run to achieve code style compliance.
ofCodeStyleGuard is intended to run on the [OpenShift](https://openshift.redhat.com) PaaS or locally.

## Requirements
* Python 2.6
* [PyGithub](https://github.com/jacquev6/PyGithub)
* Flask
* Requests
* git
* [uncrustify>=0.58](http://uncrustify.sourceforge.net/) or whatever your style script uses

## License
The code in this repository is available under the MIT License (see license.md).

Copyright (c) 2013- Christoph Buchner
