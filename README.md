# ofCodeStyleGuard

ofCodeStyleGuard continually checks openFrameworks commits for code style compliance.
It listens for notifications from Github webhooks which indicate fresh PR activity, and checks the resulting PR, generating a `.patch` file. 
This file can easily be applied by the PR submitter (using `git apply --index my.patch`) to achieve code style compliance.

Additional checks of PRs can manually be requested by visiting the URL `http://ofcodestyleguard-bilderbuchi.rhcloud.com/check?pr=<PR-number>`

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

Copyright (c) Christoph Buchner

## Notes
### Webhook
When creating the webhook in the repo, change the webhook `event` type to `pull_request` (see [here](http://chloky.com/github-pull-req-webhook/)).
This will fire any time a Pull Request is opened, closed, or synchronized (updated due to a new push in the branch that the pull request is tracking).
For example, you can do this with PyGithub like this:

```
hook= myRepo.get_hook(id)
myOF.create_hook(hook.name,hook.config, ['pull_request'],hook.active)
```

### Github Status API
* Only the latest status is displayed for a commit. This means you can't use the [Status API](http://developer.github.com/v3/repos/statuses/) to indicate more than one kind of information (e.g. build server results).
* The best way to do the authentication is via OAutch authentication tokens (instead of user/password). 
* You will need the auth scope: `repo:status`
* An auth token can be created e.g. with PyGithub like this:

```
myuser=g.get_user() myuser.create_authorization(['repo:status'],'Codestyle_status_access')
```
### Trigger sync locally
If you got `ofCodeStyleGuard` running locally, a run can be triggered from the console with `http POST http://127.0.0.1:4896`.

### OpenShift

* You can install external applications (in our case, uncrustify), by compiling it locally, putting it into $OPENSHIFT_DATA_DIR somewhere, and adding the containing directory to $PATH in the `pre_start_python-2.6` action hook.
* You have to upload the file containing your API token separately. For security reasons, this should not be included in your git repository.
