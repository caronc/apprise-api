# Apprise API

Take advantage of [Apprise](https://github.com/caronc/apprise) through your network with a user-friendly API.

- Send notifications to more than 100+ services.
- An incredibly lightweight gateway to Apprise.
- A production ready micro-service at your disposal.
- A Simple Website to verify and test your configuration with.

Apprise API was designed to easily fit into existing (and new) eco-systems that are looking for a simple notification solution.

[![Paypal](https://img.shields.io/badge/paypal-donate-green.svg)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=MHANV39UZNQ5E)
[![Follow](https://img.shields.io/twitter/follow/l2gnux)](https://twitter.com/l2gnux/)<br/>
[![Discord](https://img.shields.io/discord/558793703356104724.svg?colorB=7289DA&label=Discord&logo=Discord&logoColor=7289DA&style=flat-square)](https://discord.gg/MMPeN2D)
[![Build Status](https://github.com/caronc/apprise-api/actions/workflows/tests.yml/badge.svg)](https://github.com/caronc/apprise-api/actions/workflows/tests.yml)
[![CodeCov Status](https://codecov.io/github/caronc/apprise-api/branch/master/graph/badge.svg)](https://codecov.io/github/caronc/apprise-api)
[![Docker Pulls](https://img.shields.io/docker/pulls/caronc/apprise.svg?style=flat-square)](https://hub.docker.com/r/caronc/apprise)

## Screenshots

There is a small built-in *Configuration Manager* that can be optionally accessed through your web browser allowing you to create and save as many configurations as you'd like. Each configuration is differentiated by a unique `{KEY}` that you decide on:<br/>
![Screenshot of GUI - Using Keys](https://raw.githubusercontent.com/caronc/apprise-api/master/Screenshot-1.png)<br/>

Below is a screenshot of how you can assign your Apprise URLs to your `{KEY}`. You can define both TEXT or YAML [Apprise configurations](https://github.com/caronc/apprise/wiki/config).<br/>
![Screenshot of GUI - Configuration](https://raw.githubusercontent.com/caronc/apprise-api/master/Screenshot-2.png)

Below is a screenshot of the review tab where you can preview what Apprise URL(s) got loaded from your defined configuration. It also allows you to view the tags associated with them (if any). Should you chose to send a test notification via this API, you can select the tags in advance you wish to target from here.<br/>
![Screenshot of GUI - Review](https://raw.githubusercontent.com/caronc/apprise-api/master/Screenshot-3.png)

With configuration in place, you'll be able to use the *Notification* tab to send a test message to one or more of the services you defined in your configuration. You can also select from the tags (if any) you pre-assigned to your URLs defined. If you did not define any tags with you configured URLs, then you do not need to identify any here. You can use the tag `all` to notify all of your services regardless of what tag had otherwise been assigned to them (if any at all).<br/>
![Screenshot of GUI - Notifications](https://raw.githubusercontent.com/caronc/apprise-api/master/Screenshot-4.png)

At the end of the day, the GUI just simply offers a user friendly interface to the same API developers can directly interface with if they wish to.

## Installation

The following options should allow you to access the API at: `http://localhost:8000/` from your browser.

Using [dockerhub](https://hub.docker.com/r/caronc/apprise) you can do the following:

```bash
# Retrieve container
docker pull caronc/apprise:latest

# Start it up:
# /config is used for a persistent store, you do not have to mount
#         this if you don't intend to use it.
# /plugin is used for a location you can add your own custom apprise plugins.
#         You do not have to mount this if you don't intend to use it.
# /attach is used for file attachments
#
# The below example sets a the APPRISE_WORKER_COUNT to a small value (over-riding
# a full production environment setting).  This may be all that is needed for
# a light-weight self hosted solution.
#
# setting APPRISE_STATEFUL_MODE to simple allows you to map your defined {key}
# straight to a file found in the `/config` path.  In simple home configurations
# this is sometimes the ideal expectation.
docker run --name apprise \
   -p 8000:8000 \
   -v /var/lib/apprise/config:/config \
   -v /var/lib/apprise/plugin:/plugin \
   -v /var/lib/apprise/attach:/attach \
   -e APPRISE_STATEFUL_MODE=simple \
   -e APPRISE_WORKER_COUNT=1 \
   -d caronc/apprise:latest
```

You can also choose to build yourself a custom version after checking out the source code. This is sometimes useful when you want to make a change to the source code and try it out.
A common change one might make is to update the Dockerfile to point to the master branch of Apprise instead of using the stable version.
```bash
# Setup your environment the way you like
docker build -t apprise/local:latest -f Dockerfile .

# Launch your instance
docker run --name apprise \
   -p 8000:8000 \
   -e APPRISE_STATEFUL_MODE=simple \
   -e APPRISE_WORKER_COUNT=1 \
   -d apprise/local:latest
```
A `docker-compose.yml` file is already set up to grant you an instant production ready simulated environment:

```bash
# Docker Compose
docker-compose up
```

### Config Directory Permissions

Under the hood, An NginX services is reading/writing your configuration files as the user (and group) `www-data` which generally has the id of `33`.  In preparation so that you don't get the error: `An error occured saving configuration.` consider also setting up your local `/var/lib/apprise/config` permissions as:

```bash
# Create a user/group (if one doesn't already exist) owned
# by the user and group id of 33
id 33 &>/dev/null || sudo useradd \
   --system --no-create-home --shell /bin/false \
    -u 33 -g 33 www-data

# Securely set the directory limiting access to only those who
# are part of the www-data group:
sudo chmod 770 -R /var/lib/apprise/config
sudo chown 33:33 -R /var/lib/apprise/config

# Now optionally add yourself to the group if you wish to be able to view
# contents.
sudo usermod -a -G 33 $(whoami)

# You may need to log out and back in again for the above usermod
# to reflect on you.  Alternatively you can just type the following
# and it will work as a temporary solution:
sudo su - $(whoami)
```

Alternatively a dirty solution is to just set the directory with full read/write permissions (which is not ideal in a production environment):

```bash
# Grant full permission to the local directory you're saving your
# Apprise configuration to:
chmod 777 /var/lib/apprise/config
```

## Dockerfile Details

The following architectures are supported: `amd64`, `arm/v7`, and `arm64`. The following tags can be used:
- `latest`: Points to the latest stable build.
- `edge`: Points to the last push to the master branch.

## Apprise URLs

📣 In order to trigger a notification, you first need to define one or more [Apprise URLs](https://github.com/caronc/apprise/wiki) to support the services you wish to leverage. Visit <https://github.com/caronc/apprise/wiki> to see the ever-growing list of the services supported today.

## API Details

### Health Checks

You can perform status or health checks on your server configuration by accessing `/status`.

| Path         | Method | Description |
|------------- | ------ | ----------- |
| `/status` |  GET  | Simply returns a server status.  The server http response code is a `200` if the server is working correcty and a `417` if there was an unexpected issue.  You can set the `Accept` header to `application/json` or `text/plain` for different response outputs.

Below is a sample of just a simple text response:
```bash
# Request a general text response
# Output will read `OK` if everything is fine, otherwise it will return
# one or more of the following separated by a comma:
#  - ATTACH_PERMISSION_ISSUE: Can not write attachments (likely a permission issue)
#  - CONFIG_PERMISSION_ISSUE: Can not write configuration (likely a permission issue)
curl -X GET http://localhost:8000/status
```

Below is a sample of a JSON response:
```bash
curl -X GET -H "Accept: application/json" http://localhost:8000/status
```
The above output may look like this:
```json
{
  "config_lock": false,
  "status": {
    "can_write_config": true,
    "can_write_attach": true,
    "details": ["OK"]
  }
}
```

- The `config_lock` always cross references if the `APPRISE_CONFIG_LOCK` is enabled or not.
- The `status.can_write_config` defines if the configuration directory is writable or not.  If the environment variable `APPRISE_STATEFUL_MODE` is set to `disabled`, this value will always read `false` and it will not impact the `status.details`
- The `status.can_write_attach` defines if the attachment directory is writable or not.  If the environment variable `APPRISE_ATTACH_SIZE` or `APPRISE_MAX_ATTACHMENTS` is set to `0` (zero) or lower, this value will always read `false` and it will not impact the `status.details`.
- The `status.details` identifies the overall status. If there is more then 1 issue to report here, they will all show in this list.  In a working orderly environment, this will always be set to `OK` and the http response type will be `200`.

### Stateless Solution

Some people may wish to only have a sidecar solution that does require use of any persistent storage.  The following API endpoint can be used to directly send a notification of your choice to any of the [supported services by Apprise](https://github.com/caronc/apprise/wiki) without any storage based requirements:

| Path         | Method | Description |
|------------- | ------ | ----------- |
| `/notify/` |  POST  | Sends one or more notifications to the URLs identified as part of the payload, or those identified in the environment variable `APPRISE_STATELESS_URLS`. <br/>*Payload Parameters*<br/>📌 **urls**: One or more URLs identifying where the notification should be sent to. If this field isn't specified then it automatically assumes the `settings.APPRISE_STATELESS_URLS` value or `APPRISE_STATELESS_URLS` environment variable.<br/>📌 **body**: Your message body. This is a required field.<br/>📌 **title**: Optionally define a title to go along with the *body*.<br/>📌 **type**: Defines the message type you want to send as.  The valid options are `info`, `success`, `warning`, and `failure`. If no *type* is specified then `info` is the default value used.<br/>📌 **format**: Optionally identify the text format of the data you're feeding Apprise. The valid options are `text`, `markdown`, `html`. The default value if nothing is specified is `text`.

Here is a *stateless* example of how one might send a notification (using `/notify/`):

```bash
# Send your notifications directly
curl -X POST -d 'urls=mailto://user:pass@gmail.com&body=test message' \
    http://localhost:8000/notify

# Send a notification with an attachment:
curl -X POST \
    -F 'urls=mailto://user:pass@gmail.com' \
    -F 'body=test message' \
    -F attach=@Screenshot-1.png \
    http://localhost:8000/notify

# Send multiple attachments; just make sure the attach keyword is unique:
curl -X POST \
    -F 'urls=mailto://user:pass@gmail.com' \
    -F 'body=test message' \
    -F attach1=@Screenshot-1.png \
    -F attach2=@/my/path/to/Apprise.doc \
    http://localhost:8000/notify

# This example shows how you can place the body among other parameters
# in the GET parameter and not the payload as another option.
curl -X POST -d 'urls=mailto://user:pass@gmail.com&body=test message' \
    -F @/path/to/your/attachment \
    http://localhost:8000/notify

# The body is not required if an attachment is provided:
curl -X POST -d 'urls=mailto://user:pass@gmail.com' \
    -F @/path/to/your/attachment \
    http://localhost:8000/notify

# Send your notifications directly using JSON
curl -X POST -d '{"urls": "mailto://user:pass@gmail.com", "body":"test message"}' \
    -H "Content-Type: application/json" \
    http://localhost:8000/notify

# attach= is an alias of attachment=
# Send a notification with a URL based attachment
curl -X POST \
    -F 'urls=mailto://user:pass@gmail.com' \
    -F attach=attach=https://raw.githubusercontent.com/caronc/apprise/master/apprise/assets/themes/default/apprise-logo.png \
    http://localhost:8000/notify
```

You can also send notifications that are URLs.  Apprise will download the item so that it can send it along to all end points that should be notified about it.
```bash
# Use the 'attachment' parameter and send along a web request
curl -X POST \
    -F 'urls=mailto://user:pass@gmail.com' \
    -F attachment=https://i.redd.it/my2t4d2fx0u31.jpg \
    http://localhost:8000/notify

# To send more then one URL, the following would work:
curl -X POST \
    -F 'urls=mailto://user:pass@gmail.com' \
    -F attachment=https://i.redd.it/my2t4d2fx0u31.jpg \
    -F attachment=https://path/to/another/remote/file.pdf \
    http://localhost:8000/notify

# Finally feel free to mix and match local files with external ones:
curl -X POST \
    -F 'urls=mailto://user:pass@gmail.com' \
    -F attachment=https://i.redd.it/my2t4d2fx0u31.jpg \
    -F attachment=https://path/to/another/remote/file.pdf \
    -F @/path/to/your/local/file/attachment \
    http://localhost:8000/notify
```

### Persistent (Stateful) Storage Solution

You can pre-save all of your Apprise configuration and/or set of Apprise URLs and associate them with a `{KEY}` of your choosing. Once set, the configuration persists for retrieval by the `apprise` [CLI tool](https://github.com/caronc/apprise/wiki/CLI_Usage) or any other custom integration you've set up. The built in website with comes with a user interface that you can use to leverage these API calls as well. Those who wish to build their own application around this can use the following API end points:

| Path         | Method | Description |
|------------- | ------ | ----------- |
| `/add/{KEY}` |  POST  | Saves Apprise Configuration (or set of URLs) to the persistent store.<br/>*Payload Parameters*<br/>📌 **urls**: Define one or more Apprise URL(s) here. Use a comma and/or space to separate one URL from the next.<br/>📌 **config**: Provide the contents of either a YAML or TEXT based Apprise configuration.<br/>📌 **format**: This field is only required if you've specified the *config* parameter. Used to tell the server which of the supported (Apprise) configuration types you are passing. Valid options are *text* and *yaml*. This path does not work if `APPRISE_CONFIG_LOCK` is set.
| `/del/{KEY}` |  POST  | Removes Apprise Configuration from the persistent store. This path does not work if `APPRISE_CONFIG_LOCK` is set.
| `/get/{KEY}` |  POST  | Returns the Apprise Configuration from the persistent store.  This can be directly used with the *Apprise CLI* and/or the *AppriseConfig()* object ([see here for details](https://github.com/caronc/apprise/wiki/config)). This path does not work if `APPRISE_CONFIG_LOCK` is set.
| `/notify/{KEY}` |  POST  | Sends notification(s) to all of the end points you've previously configured associated with a *{KEY}*.<br/>*Payload Parameters*<br/>📌 **body**: Your message body. This is the *only* required field.<br/>📌 **title**: Optionally define a title to go along with the *body*.<br/>📌 **type**: Defines the message type you want to send as.  The valid options are `info`, `success`, `warning`, and `failure`. If no *type* is specified then `info` is the default value used.<br/>📌 **tag**: Optionally notify only those tagged accordingly. Use a comma (`,`) to `OR` your tags and a space (` `) to `AND` them. More details on this can be seen documented below.<br/>📌 **format**: Optionally identify the text format of the data you're feeding Apprise. The valid options are `text`, `markdown`, `html`. The default value if nothing is specified is `text`.
| `/json/urls/{KEY}` |  GET  | Returns a JSON response object that contains all of the URLS and Tags associated with the key specified.
| `/details` |  GET  | Set the `Accept` Header to `application/json` and retrieve a JSON response object that contains all of the supported Apprise URLs. See [here for more details](https://github.com/caronc/apprise/wiki/Development_Apprise_Details#apprise-details)

As an example, the `/json/urls/{KEY}` response might return something like this:

```json
{
   "tags": ["devops", "admin", "me"],
   "urls": [
      {
         "url": "slack://TokenA/TokenB/TokenC",
         "tags": ["devops", "admin"]
      },
      {
         "url": "discord://WebhookID/WebhookToken",
         "tags": ["devops"]
      },
      {
         "url": "mailto://user:pass@gmail.com",
         "tags": ["me"]
      }
   ]
}
```

You can pass in attributes to the `/json/urls/{KEY}` such as `privacy=1` which hides the passwords and secret tokens when returning the response.  You can also set `tag=` and filter the returned results based on a comma separated set of tags. if no `tag=` is specified, then `tag=all` is used as the default.

Note, if `APPRISE_CONFIG_LOCK` is set, then `privacy=1` is always enforced preventing credentials from being leaked.

Here is an example using `curl` as to how someone might send a notification to everyone associated with the tag `abc123` (using `/notify/{key}`):

```bash
# Send notification(s) to a {key} defined as 'abc123'
curl -X POST -d "body=test message" \
    http://localhost:8000/notify/abc123

# Here is the same request but using JSON instead:
curl -X POST -d '{"body":"test message"}' \
    -H "Content-Type: application/json" \
    http://localhost:8000/notify/abc123

# Send attachments:
curl -X POST \
    -F 'urls=mailto://user:pass@gmail.com' \
    -F 'body=test message' \
    -F attach1=@Screenshot-1.png \
    -F attach2=@/my/path/to/Apprise.doc \
    http://localhost:8000/notify/abc123

# attach= is an alias of attachment=
# Send a notification with a URL based attachment
curl -X POST \
    -F 'urls=mailto://user:pass@gmail.com' \
    -F attach=attach=https://raw.githubusercontent.com/caronc/apprise/master/apprise/assets/themes/default/apprise-logo.png \
    http://localhost:8000/notify/abc123
```

🏷️ Leveraging *tagging* allows you to associate one or more tags (or categories) with your Apprise URLs.  By doing this, notifications only need to be referred to by their easy to remember notify tag name such as `devops`, `admin`, `family`, etc. You can very easily group more than one notification service under the same *tag* allowing you to notify a group of services at once.  This is accomplished through configuration files ([documented here](https://github.com/caronc/apprise/wiki/config)) that can be saved to the persistent storage previously associated with a `{KEY}`.

```bash
# Send notification(s) to a {KEY} defined as 'abc123'
# but only notify the URLs associated with the 'devops' tag
curl -X POST -d 'tag=devops&body=test message' \
    http://localhost:8000/notify/abc123

# Here is the same request but using JSON instead:
curl -X POST -d '{"tag":"devops", "body":"test message"}' \
    -H "Content-Type: application/json" \
    http://localhost:8000/notify/abc123
```

### Tagging

Tagging is one of the things that makes Apprise super handy and easy to use.  Not only can you group one or more notifications together (all sharing the same tag), but you can assign multiple tags to the same URL and trigger it through crafted and selected tag expressions.

|  Example              | Effect                         |
| --------------------- | ------------------------------ |
| TagA                  |  TagA
| TagA, TagB            |  TagA **OR** TagB
| TagA TagC, TagB       |  (TagA **AND** TagC) **OR** TagB
| TagB TagC             |  TagB **AND** TagC

```bash
# 'AND' Example
# Send notification(s) to a {KEY} defined as 'abc123'
# Notify the URLs associated with the 'devops' and 'after-hours' tag
# The 'space' acts as an 'AND' You can also use '+' character (in spot of the
# space to achieve the same results)
curl -X POST -d '{"tag":"devops after-hours", "body":"repo outage"}' \
    -H "Content-Type: application/json" \
    http://localhost:8000/notify/abc123


# 'OR' Example
# Send notification(s) to a {KEY} defined as 'def456'
# Notify the URLs associated with the 'dev' OR 'qa' tag
# The 'comma' acts as an 'OR'.  The whitespace around the comma is ignored (if
# defined) You can also use '+' character (in spot of the space to achieve the
# same results)
curl -X POST -d '{"tag":"dev, qa", "body":"bug #000123 is back :("}' \
    -H "Content-Type: application/json" \
    http://localhost:8000/notify/def456


# 'AND' and 'OR' Example
# Send notification(s) to a {KEY} defined as 'projectX'
# Notify the URLs associated with the 'leaders AND teamA' AND additionally
# the 'leaders AND teamB'.
curl -X POST -d '{"tag":"leaders teamA, leaders teamB", "body":"meeting now"}' \
    -H "Content-Type: application/json" \
    http://localhost:8000/notify/projectX
```
### API Response Codes

|  HTTP Code | Name                  | Effect                         |
| ---------- | --------------------- | ------------------------------ |
| 200        | ok                    | Notification was sent
| 204        | no content            | There was no configuration (or it was empty) found by the specified `{KEY}`
| 400        | bad request           | Your API call did not conform to what was documented here
| 405        | method not accepted   | Your API call identified an action that has been disabled due to the Server configuration (such as a `apprise://` `APPRISE_RECURSION_MAX` being exceeded).
| 424        | failed dependency     | At least one notification could not be sent.  This can be due to<br/> - Not all notifications intended to be actioned could follow through (due to upstrem failures).<br/>You didn't idenify a tag associated with what was defined in your configuration.<br/>The tag(s) you specified do not match with those defined in your configuration.
| 431        | fields too large      | This can happen if you're payload is larger then 3MB (default value).  See `APPRISE_UPLOAD_MAX_MEMORY_SIZE` to adjust this.
| 500        | internal server error | This can occur if there was an issue saving your configuration to disk (usually the cause of permission issues).


### API Notes

- `{KEY}` must be 1-128 alphanumeric characters in length. In addition to this, the underscore (`_`) and dash (`-`) are also accepted.
  - Consider using keys like `sha1`, `sha512`, `uuid`, etc to secure shared namespaces if you wish to open your platform to others. Or keep it simple in a controlled environment and just use the default string `apprise` as your key (and as illustrated in the examples above). You can over-ride this default value by setting the `APPRISE_DEFAULT_CONFIG_ID`(see below).
- Specify the `Content-Type` of `application/json` to use the JSON support otherwise the default expected format is `application/x-www-form-urlencoded` (whether it is specified or not).
- There is no authentication (or SSL encryption) required to use this API; this is by design. The intention here is to be a light-weight and fast micro-service.
- There are no additional dependencies (such as database requirements, etc) should you choose to use the optional persistent store (mounted as `/config`).

### Environment Variables

The use of environment variables allow you to provide over-rides to default settings.

| Variable             | Description |
|--------------------- | ----------- |
| `APPRISE_DEFAULT_THEME` | Can be set to `light` or `dark`; it defaults to `light` if not otherwise provided.  The theme can be toggled from within the website as well.
| `APPRISE_DEFAULT_CONFIG_ID` | Defaults to `apprise`.   This is the presumed configuration ID you always default to when accessing the configuration manager via the website.
| `APPRISE_CONFIG_DIR` | Defines an (optional) persistent store location of all configuration files saved. By default:<br/> - Configuration is written to the `apprise_api/var/config` directory when just using the _Django_ `manage runserver` script. However for the path for the container is `/config`.
| `APPRISE_ATTACH_DIR` | The directory the uploaded attachments are placed in. By default:<br/> - Attachments are written to the `apprise_api/var/attach` directory when just using the _Django_ `manage runserver` script. However for the path for the container is `/attach`.
| `APPRISE_ATTACH_SIZE` | Over-ride the attachment size (defined in MB). By default it is set to `200` (Megabytes). You can set this up to a maximum value of `500` which is the restriction in place for NginX (internal hosting ervice) at this time.  If you set this to zero (`0`) then attachments will not be passed along even if provided.
| `APPRISE_UPLOAD_MAX_MEMORY_SIZE` | Over-ride the in-memory accepted payload size (defined in MB). By default it is set to `3` (Megabytes). There is no reason the HTTP payload (excluding attachments) should exceed this limit.  This value is only configurable for those who have edge cases where there are exceptions to this rule.
| `APPRISE_STATELESS_URLS` | For a non-persistent solution, you can take advantage of this global variable. Use this to define a default set of Apprise URLs to notify when using API calls to `/notify`.  If no `{KEY}` is defined when calling `/notify` then the URLs defined here are used instead. By default, nothing is defined for this variable.
| `APPRISE_STATEFUL_MODE` | This can be set to the following possible modes:<br/>📌 **hash**: This is also the default.  It stores the server configuration in a hash formatted that can be easily indexed and compressed.<br/>📌 **simple**: Configuration is written straight to disk using the `{KEY}.cfg` (if `TEXT` based) and `{KEY}.yml` (if `YAML` based).<br/>📌 **disabled**: Straight up deny any read/write queries to the servers stateful store.  Effectively turn off the Apprise Stateful feature completely.
| `APPRISE_CONFIG_LOCK` | Locks down your API hosting so that you can no longer delete/update/access stateful information. Your configuration is still referenced when stateful calls are made to `/notify`.  The idea of this switch is to allow someone to set their (Apprise) configuration up and then as an added security tactic, they may choose to lock their configuration down (in a read-only state). Those who use the Apprise CLI tool may still do it, however the `--config` (`-c`) switch will not successfully reference this access point anymore. You can however use the `apprise://` plugin without any problem ([see here for more details](https://github.com/caronc/apprise/wiki/Notify_apprise_api)). This defaults to `no` and can however be set to `yes` by simply defining the global variable as such.
| `APPRISE_DENY_SERVICES` | A comma separated set of entries identifying what plugins to deny access to. You only need to identify one schema entry associated with a plugin to in turn disable all of it.  Hence, if you wanted to disable the `glib` plugin, you do not need to additionally include `qt` as well since it's included as part of the (`dbus`) package; consequently specifying `qt` would in turn disable the `glib` module as well (another way to accomplish the same task).  To exclude/disable more the one upstream service, simply specify additional entries separated by a `,` (comma) or ` ` (space). The `APPRISE_DENY_SERVICES` entries are ignored if the `APPRISE_ALLOW_SERVICES` is identified. By default, this is initialized to `windows, dbus, gnome, macosx, syslog` (blocking local actions from being issued inside of the docker container)
| `APPRISE_ALLOW_SERVICES` | A comma separated set of entries identifying what plugins to allow access to. You may only use alpha-numeric characters as is the restriction of Apprise Schemas (schema://) anyway.  To exclusively include more the one upstream service, simply specify additional entries separated by a `,` (comma) or ` ` (space). The `APPRISE_DENY_SERVICES` entries are ignored if the `APPRISE_ALLOW_SERVICES` is identified.
| `SECRET_KEY`       | A Django variable acting as a *salt* for most things that require security. This API uses it for the hash sequences when writing the configuration files to disk (`hash` mode only).
| `ALLOWED_HOSTS`    | A list of strings representing the host/domain names that this API can serve. This is a security measure to prevent HTTP Host header attacks, which are possible even under many seemingly-safe web server configurations. By default this is set to `*` allowing any host. Use space to delimit more than one host.
| `APPRISE_PLUGIN_PATHS` | Apprise supports the ability to define your own `schema://` definitions and load them.  To read more about how you can create your own customizations, check out [this link here](https://github.com/caronc/apprise/wiki/decorator_notify). You may define one or more paths (separated by comma `,`) here. By default the `apprise_api/var/plugin` directory is scanned (which does not include anything). Feel free to set this to an empty string to disable any custom plugin loading.
| `APPRISE_RECURSION_MAX` | This defines the number of times one Apprise API Server can (recursively) call another.  This is to both support and mitigate abuse through [the `apprise://` schema](https://github.com/caronc/apprise/wiki/Notify_apprise_api) for those who choose to use it. When leveraged properly, you can increase this (recursion max) value and successfully load balance the handling of many notification requests through many additional API Servers.  By default this value is set to `1` (one).
| `APPRISE_WEBHOOK_URL` | Define a Webhook that Apprise should `POST` results to upon each notification call made.  This must be in the format of an `http://` or `https://` URI.  By default no URL is specified and no webhook is actioned.
| `APPRISE_WORKER_COUNT` | Over-ride the number of workers to run.  by default this is calculated `(2 * CPUS_DETECTED) + 1` [as advised by Gunicorn's website](https://docs.gunicorn.org/en/stable/design.html#how-many-workers). Hobby enthusiasts and/or users who are simply setting up Apprise to support their home (light-weight usage) may wish to set this value to `1` to limit the resources the Apprise server prepares for itself.
| `APPRISE_WORKER_TIMEOUT` | Over-ride the worker timeout value (in seconds); by default this is `300` (5 min) which should be more than enough time to send all pending notifications.
| `BASE_URL`    | Those who are hosting the API behind a proxy that requires a subpath to gain access to this API should specify this path here as well.  By default this is not set at all.
| `LOG_LEVEL`    | Adjust the log level to the console. Possible values are `CRITICAL`, `ERROR`, `WARNING`, `INFO`, and `DEBUG`.
| `DEBUG`            | This defaults to `no` and can however be set to `yes` by simply defining the global variable as such.


## Development Environment

The following should get you a working development environment to test with:

```bash
# Create a virtual environment in the same directory you
# cloned this repository to:
python -m venv .

# Activate it now:
. ./bin/activate

# install dependencies
pip install -r dev-requirements.txt -r requirements.txt

# Run a dev server (debug mode) accessible from your browser at:
# -> http://localhost:8000/
./manage.py runserver

```

Some other useful development notes:

```bash
# Check for any lint errors
flake8 apprise_api

# Run unit tests
pytest apprise_api
```

## Apprise Integration

First you'll need to have it installed:
```bash
# install apprise into your environment
pip install apprise
```

### Apprise CLI Pull Example

A scenario where you want to poll the API for your configuration:

```bash
# A simple example of the Apprise CLI
# pulling down previously stored configuration
apprise -vvv --body="test message" \
   --config=http://localhost:8000/get/{KEY}
```

You can also leverage the `import` parameter supported in Apprise configuration files if `APPRISE_CONFIG_LOCK` isn't set on the server you're accessing:

```nginx
# Linux users can place this in ~/.apprise
# Windows users can place this info in %APPDATA%/Apprise/apprise

# Swap {KEY} with your apprise key you configured on your API
import http://localhost:8000/get/{KEY}
```

Now you'll just automatically source the configuration file without the need of the `--config` switch:

```bash
# Configuration is automatically loaded from our server.
apprise -vvv --body="my notification"
```

If you used tagging, then you can notify the specific service like so:

```bash
# Configuration is automatically loaded from our server.
apprise -vvv --tag=devops \
   --body="Tell James GitLab is down again."
```


If you're server has the `APPRISE_CONFIG_LOCK` set, you can still leverage [the `apprise://` plugin](https://github.com/caronc/apprise/wiki/Notify_apprise_api) to trigger our pre-saved notifications:
```bash
# Swap {KEY} with your apprise key you configured on your API
apprise -vvv --body="There are donut's in the front hall if anyone wants any" \
   apprise://localhost:8000/{KEY}
```

Alternatively we can set this up in a configuration file and even tie our local tags to our upstream ones like so:

```nginx
# Linux users can place this in ~/.apprise
# Windows users can place this info in %APPDATA%/Apprise/apprise

# Swap {KEY} with your apprise key you configured on your API
devteam=apprise://localhost:8000/{KEY}?tags=devteam

# the only catch is you need to map your tags on the local server to the tags
# you want to pass upstream to your Apprise server using this method.
# In the above we tied the local keyword `devteam` to the apprise server using the tag `devteam`
```

We could trigger our notification to our devteam now like:

```bash
# Trigger our service:
apprise -vvv --tag=devteam \
    --body="Guys, don't forget about the audit tomorrow morning."
```

### AppriseConfig() Pull Example

Using the [Apprise Python library](https://github.com/caronc/apprise), you can easily access and load your saved configuration from this API in order to use for future notifications.

```python
import apprise

# Point our configuration to this API server:
config = apprise.AppriseConfig()

# The following only works if APPRISE_CONFIG_LOCK is not set
# if APPRISE_CONFIG_LOCK is set, you can optionally leverage the apprise://
# URL instead.
config.add('http://localhost:8000/get/{KEY}')

# Create our Apprise Instance
a = apprise.Apprise()

# Store our new configuration
a.add(config)

# Send a test message
a.notify('test message')
```

## Third Party Webhook Support
It can be understandable that third party applications can't always publish the format expected by this API tool.  To work-around this, you can re-map the fields just before they're processed.  For example; consider that we expect the follow minimum payload items for a stateful notification:
```json
{
    "body": "Message body"
}
```

But what if your tool you're using is only capable of sending:
```json
{
   "subject": "My Title",
   "payload": "My Body"
}
```

We would want to map `subject` to `title` in this case and `payload` to `body`.  This can easily be done using the `:` (colon) argument when we prepare our payload:

```bash
# Note the keyword arguments prefixed with a `:` (colon).   These
# instruct the API to map the payload (which we may not have control over)
# to align with what the Apprise API expects.
#
# We also convert `subject` to `title` too:
curl -X POST \
    -F "subject=Mesage Title" \
    -F "payload=Message Body" \
    "http://localhost:8000/notify/{KEY}?:subject=title&:payload=body"

```

Here is the JSON Version and tests out the Stateless query (which requires at a minimum the `urls` and `body`:
```bash
# We also convert `subject` to `title` too:
curl -X POST -d '{"href": "mailto://user:pass@gmail.com", "subject":"My Title", "payload":"Body"}' \
    -H "Content-Type: application/json" \
    "http://localhost:8000/notify/{KEY}?:subject=title&:payload=body&:href=urls"
```

The colon `:` prefix is the switch that starts the re-mapping rule engine.  You can do 3 possible things with the rule engine:
1. `:existing_key=expected_key`: Rename an existing (expected) payload key to one Apprise expects
1. `:existing_key=`: By setting no value, the existing key is simply removed from the payload entirely
1. `:expected_key=A value to give it`: You can also fix an expected apprise key to a pre-generated string value.

