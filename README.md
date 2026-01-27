# Apprise API

Take advantage of [Apprise](https://github.com/caronc/apprise) through your network with a user-friendly API.

- Send notifications to more than 120+ services.
- An incredibly lightweight gateway to Apprise.
- A production-ready micro-service at your disposal.
- A Simple Website to verify and test your configuration with.

Apprise API was designed to easily fit into existing (and new) ecosystems that are looking for a simple notification solution.

[![Paypal](https://img.shields.io/badge/paypal-donate-green.svg)](https://www.paypal.com/donate/?hosted_button_id=CR6YF7KLQWQ5E)
[![Follow](https://img.shields.io/twitter/follow/l2gnux)](https://twitter.com/l2gnux/)<br/>
[![Discord](https://img.shields.io/discord/558793703356104724.svg?colorB=7289DA&label=Discord&logo=Discord&logoColor=7289DA&style=flat-square)](https://discord.gg/MMPeN2D)
[![Build Status](https://github.com/caronc/apprise-api/actions/workflows/tests.yml/badge.svg)](https://github.com/caronc/apprise-api/actions/workflows/tests.yml)
[![CodeCov Status](https://codecov.io/github/caronc/apprise-api/branch/master/graph/badge.svg)](https://codecov.io/github/caronc/apprise-api)
[![Docker Pulls](https://img.shields.io/docker/pulls/caronc/apprise.svg?style=flat-square)](https://hub.docker.com/r/caronc/apprise)

Visit the [Official Documentation](https://appriseit.com/getting-started/) site for more information on the Apprise API.

## Screenshots

There is a small built-in *Configuration Manager* that can be optionally accessed through your web browser allowing you to create and save as many configurations as you'd like. Each configuration is differentiated by a unique `{KEY}` that you decide on:<br/>
![Screenshot of GUI - Using Keys](https://raw.githubusercontent.com/caronc/apprise-api/master/Screenshot-1.png)<br/>

Below is a screenshot of how you can assign your Apprise URLs to your `{KEY}`. You can define both TEXT or YAML [Apprise configurations](https://appriseit.com/config/).<br/>
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
# /config is used for a spot to write all of the configuration files
#         generated through the API. The internal persistent store lives
#         under /config/store so a single /config volume is sufficient.
# /plugin is used for a location you can add your own custom apprise plugins.
#         You do not have to mount this if you don't intend to use it.
# /attach is used for file attachments
#
# /tmp         Temporary files, suitable for `tmpfs` in hardened deployments.
#
# The below example sets a the APPRISE_WORKER_COUNT to a small value (overriding
# a full production environment setting).  This may be all that is needed for
# a light-weight self hosted solution.
#
# setting APPRISE_STATEFUL_MODE to simple allows you to map your defined {key}
# straight to a file found in the `/config` path.  In simple home configurations
# this is sometimes the ideal expectation.
#
# Set your User ID or Group ID if you wish to override the default of 1000
# in the below example, we make sure it runs as the user we created the container as

docker run --name apprise \
   -p 8000:8000 \
   --user "$(id -u):$(id -g)" \
   -v /path/to/local/config:/config \
   -v /path/to/local/plugin:/plugin \
   -v /path/to/local/attach:/attach \
   -e APPRISE_STATEFUL_MODE=simple \
   -e APPRISE_WORKER_COUNT=1 \
   -e APPRISE_ADMIN=y \
   -d caronc/apprise:latest
```

You can also choose to build yourself a custom version after checking out the source code. This is sometimes useful when you want to make a change to the source code and try it out.
A common change one might make is to update the Dockerfile to point to the master branch of Apprise instead of using the stable version.
```bash
# Setup your environment the way you like
docker build -t apprise/local:latest -f Dockerfile .

# Set up a directory you wish to store your configuration in:
mkdir -p /etc/apprise

# Launch your instance
docker run --name apprise \
   -p 8000:8000 \
   --user "$(id -u):$(id -g)" \
   -e APPRISE_STATEFUL_MODE=simple \
   -e APPRISE_WORKER_COUNT=1 \
   -e APPRISE_ADMIN=y \
   -v /etc/apprise:/config \
   -d apprise/local:latest

# Change your paths to what you want them to be, you may also wish to
# just do the following:
mkdir -p config
docker run --name apprise \
   -p 8000:8000 \
   --user "$(id -u):$(id -g)" \
   -e APPRISE_STATEFUL_MODE=simple \
   -e APPRISE_WORKER_COUNT=1 \
   -e APPRISE_ADMIN=y \
   -v ./config:/config \
   -d apprise/local:latest
```

### Docker Compose Examples

A minimal `docker-compose.yml` might look like this:

```yaml
services:
  apprise:
    image: caronc/apprise:latest
    container_name: apprise
    ports:
      - "8000:8000"
    environment:
      APPRISE_STATEFUL_MODE: simple
      APPRISE_WORKER_COUNT: "1"
      APPRISE_ADMIN: "y"
    volumes:
      - ./config:/config
      - ./plugin:/plugin
      - ./attach:/attach
```

For production deployments, do not use `docker-compose.override.yml`.
Deploy using only `docker-compose.yml`, so the container uses the immutable image and its bundled static assets.
```bash
# Pre-create the paths you will mount to
mkdir -p attach config plugin

# Ensure you've got the latest image:
docker pull caronc/apprise:latest

# Ignore override, use only the base file
PUID=$(id -u) PGID=$(id -g) \
   docker compose -f docker-compose.yml up -d
```

A more hardened configuration might look like:
```yaml
services:
  apprise:
    image: caronc/apprise:latest
    container_name: apprise
    user: "${PUID:-1000}:${PGID:-1000}"

    # Hardened runtime
    read_only: true
    security_opt:
      - no-new-privileges:true
    cap_drop:
      - ALL

    ports:
      - "8000:8000"

    environment:
      APPRISE_STATEFUL_MODE: simple
      APPRISE_WORKER_COUNT: "1"
      APPRISE_ADMIN: "y"

    # Persistent state
    volumes:
      - ./config:/config
      - ./plugin:/plugin
      - ./attach:/attach

    # Ephemeral runtime and temp files
    tmpfs:
      - /tmp
```

## Dockerfile Details

The following architectures are supported: `amd64`, `arm/v7`, and `arm64`. The following tags can be used:
- `latest`: Points to the latest stable build.
- `edge`: Points to the last push to the master branch.

### Container Runtime Model and Filesystem Layout

The Apprise API container is designed to be “container native”:

- All logs (nginx, gunicorn, supervisord) are written to `stdout` and `stderr`.
  No files are written under `/var/log`. You should use `docker logs`,
  `kubectl logs`, or your orchestrator’s log collection instead.
- Nginx and the application use `/tmp` for all temporary files
  (request bodies, proxy buffers, etc.).
- Runtime state such as pid and socket information is written under `/tmp/apprise`.

Persistent or writable locations are:

- `/config` – configuration created and managed through the API or UI.
  The internal persistent store lives under `/config/store`.
- `/attach` – uploaded attachments.
- `/plugin` – custom Apprise plugins.
- `/tmp` – temporary files, suitable for `tmpfs` in hardened deployments.

For simple deployments you only need to mount persistent storage for `/config`, `/attach`, if you want that data to survive container restarts. You only require `/plugin` if you intend to add some custom plugins into your Apprise instance for others to use.

Stateless `/notify` usage can still use `/attach` for file uploads and optionally `/plugin` for custom plugins. In those cases you can back them with either persistent volumes or ephemeral storage, depending on your needs.

## Apprise URLs

📣 In order to trigger a notification, you first need to define one or more [Apprise URLs](https://appriseit.com/services/) to support the services you wish to leverage. Visit <https://appriseit.com/services/> to see the ever-growing list of the services supported today.

## API Details

### Health Checks

You can perform status or health checks on your server configuration by accessing `/status`.

| Path         | Method | Description |
|------------- | ------ | ----------- |
| `/status` |  GET  | Simply returns a server status.  The server http response code is a `200` if the server is working correctly and a `417` if there was an unexpected issue.  You can set the `Accept` header to `application/json` or `text/plain` for different response outputs.

Below is a sample of just a simple text response:
```bash
# Request a general text response
# Output will read `OK` if everything is fine, otherwise it will return
# one or more of the following separated by a comma:
#  - ATTACH_PERMISSION_ISSUE: Can not write attachments (likely a permission issue)
#  - CONFIG_PERMISSION_ISSUE: Can not write configuration (likely a permission issue)
#  - STORE_PERMISSION_ISSUE: Can not write to persistent storage (likely a permission issue)
curl -X GET http://localhost:8000/status
```

Below is a sample of a JSON response:
```bash
curl -X GET -H "Accept: application/json" http://localhost:8000/status
```
The above output may look like this:
```json
{
  "attach_lock": false,
  "config_lock": false,
  "status": {
    "persistent_storage": true,
    "can_write_config": true,
    "can_write_attach": true,
    "details": ["OK"]
  }
}
```

- The `attach_lock` always cross references if the `APPRISE_ATTACH_SIZE` on whether or not it is `0` (zero) or less.
- The `config_lock` always cross references if the `APPRISE_CONFIG_LOCK` is enabled or not.
- The `status.persistent_storage` defines if the persistent storage is enabled or not.  If the environment variable `APPRISE_STORAGE_PATH` is empty, this value will always read `false` and it will not impact the `status.details`
- The `status.can_write_config` defines if the configuration directory is writable or not.  If the environment variable `APPRISE_STATEFUL_MODE` is set to `disabled`, this value will always read `false` and it will not impact the `status.details`
- The `status.can_write_attach` defines if the attachment directory is writable or not.  If the environment variable `APPRISE_ATTACH_SIZE`. This value will always read `false` and it will not impact the `status.details`.
- The `status.details` identifies the overall status. If there is more then 1 issue to report here, they will all show in this list.  In a working orderly environment, this will always be set to `OK` and the http response type will be `200`.

### Stateless Solution

Some people may wish to only have a sidecar solution that does require use of any persistent storage.  The following API endpoint can be used to directly send a notification of your choice to any of the [supported services by Apprise](https://appriseit.com/services/) without any storage based requirements:

| Path         | Method | Description |
|------------- | ------ | ----------- |
| `/notify/` |  POST  | Sends one or more notifications to the URLs identified as part of the payload, or those identified in the environment variable `APPRISE_STATELESS_URLS`. <br/>*Payload Parameters*<br/>📌 **urls**: One or more URLs identifying where the notification should be sent to. If this field isn't specified then it automatically assumes the `settings.APPRISE_STATELESS_URLS` value or `APPRISE_STATELESS_URLS` environment variable.<br/>📌 **body**: Your message body. This is a required field.<br/>📌 **title**: Optionally define a title to go along with the *body*.<br/>📌 **type**: Defines the message type you want to send as.  The valid options are `info`, `success`, `warning`, and `failure`. If no *type* is specified then `info` is the default value used.<br/>📌 **format**: Optionally identify the text format of the data you're feeding Apprise. The valid options are `text`, `markdown`, `html`. The default value if nothing is specified is `text`.

Stateless `/notify` calls do not require `/config`, but they do leverage `/attach` for file uploads (assuming `APPRISE_ATTACH_SIZE` is not set to `0` (zero).  You can optionally use `/plugin` if you have custom Apprise plugins you wish to use.

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
    -F attach=https://raw.githubusercontent.com/caronc/apprise/master/apprise/assets/themes/default/apprise-logo.png \
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

You can pre-save all of your Apprise configuration and/or set of Apprise URLs and associate them with a `{KEY}` of your choosing. Once set, the configuration persists for retrieval by the `apprise` [CLI tool](https://appriseit.com/guides/) or any other custom integration you've set up. The built in website with comes with a user interface that you can use to leverage these API calls as well. Those who wish to build their own application around this can use the following API end points:

| Path         | Method | Description |
|------------- | ------ | ----------- |
| `/add/{KEY}` |  POST  | Saves Apprise Configuration (or set of URLs) to the persistent store.<br/>*Payload Parameters*<br/>📌 **urls**: Define one or more Apprise URL(s) here. Use a comma and/or space to separate one URL from the next.<br/>📌 **config**: Provide the contents of either a YAML or TEXT based Apprise configuration.<br/>📌 **format**: This field is only required if you've specified the *config* parameter. Used to tell the server which of the supported (Apprise) configuration types you are passing. Valid options are *text* and *yaml*. This path does not work if `APPRISE_CONFIG_LOCK` is set.
| `/del/{KEY}` |  POST  | Removes Apprise Configuration from the persistent store. This path does not work if `APPRISE_CONFIG_LOCK` is set.
| `/cfg/{KEY}` |  POST  | Returns the Apprise Configuration from the persistent store.  This can be directly used with the *Apprise CLI* and/or the *AppriseConfig()* object ([see here for details](https://appriseit.com/config/)). This path does not work if `APPRISE_CONFIG_LOCK` is set. This is an alias of `/get/{KEY}` (identified next).
| `/get/{KEY}` |  POST  | Returns the Apprise Configuration from the persistent store.  This can be directly used with the *Apprise CLI* and/or the *AppriseConfig()* object ([see here for details](https://appriseit.com/config/)). This path does not work if `APPRISE_CONFIG_LOCK` is set. This is also provided via `/cfg/{KEY}` as an alias.
| `/notify/{KEY}` |  POST  | Sends notification(s) to all of the end points you've previously configured associated with a *{KEY}*.<br/>*Payload Parameters*<br/>📌 **body**: Your message body. This is the *only* required field.<br/>📌 **title**: Optionally define a title to go along with the *body*.<br/>📌 **type**: Defines the message type you want to send as.  The valid options are `info`, `success`, `warning`, and `failure`. If no *type* is specified then `info` is the default value used.<br/>📌 **tag**: Optionally notify only those tagged accordingly. Use a comma (`,`) to `OR` your tags and a space (` `) to `AND` them. More details on this can be seen documented below.<br/>📌 **format**: Optionally identify the text format of the data you're feeding Apprise. The valid options are `text`, `markdown`, `html`. The default value if nothing is specified is `text`.
| `/json/urls/{KEY}` |  GET  | Returns a JSON response object that contains all of the URLS and Tags associated with the key specified.
| `/details` |  GET  | Set the `Accept` Header to `application/json` and retrieve a JSON response object that contains all of the supported Apprise URLs. See [here for more details](https://appriseit.com/dev/apprise_details/)
| `/metrics` |  GET  | Prometheus endpoint for _basic_ Metrics Collection & Analysis and/or Observability.

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
    -F attach=https://raw.githubusercontent.com/caronc/apprise/master/apprise/assets/themes/default/apprise-logo.png \
    http://localhost:8000/notify/abc123
```

🏷️ Leveraging *tagging* allows you to associate one or more tags (or categories) with your Apprise URLs.  By doing this, notifications only need to be referred to by their easy to remember notify tag name such as `devops`, `admin`, `family`, etc. You can very easily group more than one notification service under the same *tag* allowing you to notify a group of services at once.  This is accomplished through configuration files ([documented here](https://appriseit.com/config/)) that can be saved to the persistent storage previously associated with a `{KEY}`.

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
| 421        | misdirected request   | This is the value returned by any web requests made to the general website if `APPRISE_API_ONLY` is set to `yes`.  Otherwise this return value is not used.
| 424        | failed dependency     | At least one notification could not be sent.  This can be due to<br/> - Not all notifications intended to be actioned could follow through (due to upstream failures).<br/>You didn't idenify a tag associated with what was defined in your configuration.<br/>The tag(s) you specified do not match with those defined in your configuration.
| 431        | fields too large      | This can happen if you're payload is larger then 3MB (default value).  See `APPRISE_UPLOAD_MAX_MEMORY_SIZE` to adjust this.
| 500        | internal server error | This can occur if there was an issue saving your configuration to disk (usually the cause of permission issues).


### API Notes

- `{KEY}` must be 1-128 alphanumeric characters in length. In addition to this, the underscore (`_`) and dash (`-`) are also accepted.
  - Consider using keys like `sha1`, `sha512`, `uuid`, etc to secure shared namespaces if you wish to open your platform to others. Or keep it simple in a controlled environment and just use the default string `apprise` as your key (and as illustrated in the examples above). You can override this default value by setting the `APPRISE_DEFAULT_CONFIG_ID`(see below).
- Specify the `Content-Type` of `application/json` to use the JSON support otherwise the default expected format is `application/x-www-form-urlencoded` (whether it is specified or not).
- There is no authentication (or SSL encryption) required to use this API; this is by design. The intention here is to be a light-weight and fast micro-service.
- There are no additional dependencies (such as database requirements, etc) should you choose to use the optional persistent store (mounted as `/config`).

### Environment Variables

The use of environment variables allow you to provide overrides to default settings.

| Variable             | Description |
|--------------------- | ----------- |
| `PUID` | The User ID you wish the Apprise services under the hood to run as when the container starts as root and no explicit `--user` / `user:` has been set. The default is `1000` if not otherwise specified.
| `PGID` | The Group ID used in the same scenario as `PUID`. If the container is started with an explicit `--user` or `user:`, that value takes precedence and `PUID` / `PGID` are not consulted for process privileges.
| `IPV4_ONLY` | Force an all IPv4 only environment (default supports both IPV4 and IPv6).  Nothing is done if `IPV6_ONLY` is also set as this creates an ambiguous setup.  **Note**: This only works if the container is not explicitly started with `--user` or `user:`.
| `IPV6_ONLY` | Force an all IPv6 only environment (default supports both IPv4 and IPv6).  Nothing is done if `IPV4_ONLY` is also set as this creates an ambiguous setup.  **Note**: This only works if the container is not explicitly started with `--user` or `user:`.
| `HTTP_PORT` | Force the default listening port to be something other then `8000` within the Docker container. **Note**: This only works if the container is not explicitly started with `--user` or `user:`.
| `STRICT_MODE` | Applicable only to container deployments; if this is set to `yes`, the NginX instance will not return content on any invalid or unsupported request.  This is incredibly useful for those hosting Apprise publicly and pairs nicely with fail2ban.  By default, the system does not operate in this strict mode.
| `APPRISE_DEFAULT_THEME` | Can be set to `light` or `dark`; it defaults to `light` if not otherwise provided.  The theme can be toggled from within the website as well.
| `APPRISE_DEFAULT_CONFIG_ID` | Defaults to `apprise`.   This is the presumed configuration ID you always default to when accessing the configuration manager via the website.
| `APPRISE_CONFIG_DIR` | Defines an (optional) persistent store location of all configuration files saved. By default:<br/> - Configuration is written to the `apprise_api/var/config` directory when just using the _Django_ `manage runserver` script. However for the path for the container is `/config`.
| `APPRISE_STORAGE_DIR` | Defines an (optional) persistent store location of all cache files saved. By default persistent storage is written into the `<APPRISE_CONFIG_DIR>/store`.
| `APPRISE_STORAGE_MODE` | Defines the storage mode to use.  If no `APPRISE_STORAGE_DIR` is identified, then this is set to `memory` in all circumtances regardless what it might otherwise be set to. The possible options are:<br/>📌 **auto**: This is also the default. Writes cache files on demand only. <br/>📌 **memory**: Persistent storage is disabled; local memory is used for simple internal references. This is effectively the behavior of Apprise of versions 1.8.1 and earlier.<br/>📌 **flush**: A bit more i/o intensive then `auto`.  Content is written to disk constantly if changed in anyway. This mode is still experimental.
| `APPRISE_STORAGE_UID_LENGTH` | Defines the unique key lengths used to identify an Apprise URL.  By default this is set to `8`.  Value can not be set to a smaller value then `2` or larger then `64`.
| `APPRISE_STATELESS_STORAGE` | Allow stateless URLs (in addition to stateful) to also leverage persistent storage. This defaults to `no` and can however be set to `yes` by simply defining the global variable as such.
| `APPRISE_ATTACH_DIR` | The directory the uploaded attachments are placed in. By default:<br/> - Attachments are written to the `apprise_api/var/attach` directory when just using the _Django_ `manage runserver` script. However for the path for the container is `/attach`.
| `APPRISE_ATTACH_SIZE` | Over-ride the attachment size (defined in MB). By default it is set to `200` (Megabytes). You can set this up to a maximum value of `500` which is the restriction in place for NginX (internal hosting ervice) at this time.  If you set this to zero (`0`) then attachments will not be passed along even if provided.
| `APPRISE_UPLOAD_MAX_MEMORY_SIZE` | Over-ride the in-memory accepted payload size (defined in MB). By default it is set to `3` (Megabytes). There is no reason the HTTP payload (excluding attachments) should exceed this limit.  This value is only configurable for those who have edge cases where there are exceptions to this rule.
| `APPRISE_STATELESS_URLS` | For a non-persistent solution, you can take advantage of this global variable. Use this to define a default set of Apprise URLs to notify when using API calls to `/notify`.  If no `{KEY}` is defined when calling `/notify` then the URLs defined here are used instead. By default, nothing is defined for this variable.
| `APPRISE_STATEFUL_MODE` | This can be set to the following possible modes:<br/>📌 **hash**: This is also the default.  It stores the server configuration in a hash formatted that can be easily indexed and compressed.<br/>📌 **simple**: Configuration is written straight to disk using the `{KEY}.cfg` (if `TEXT` based) and `{KEY}.yml` (if `YAML` based).<br/>📌 **disabled**: Straight up deny any read/write queries to the servers stateful store.  Effectively turn off the Apprise Stateful feature completely.
| `APPRISE_CONFIG_LOCK` | Locks down your API hosting so that you can no longer delete/update/access stateful information. Your configuration is still referenced when stateful calls are made to `/notify`.  The idea of this switch is to allow someone to set their (Apprise) configuration up and then as an added security tactic, they may choose to lock their configuration down (in a read-only state). Those who use the Apprise CLI tool may still do it, however the `--config` (`-c`) switch will not successfully reference this access point anymore. You can however use the `apprise://` plugin without any problem ([see here for more details](https://appriseit.com/services/apprise_api/)). This defaults to `no` and can however be set to `yes` by simply defining the global variable as such.
| `APPRISE_ADMIN` | Enables admin mode. This removes the distinction between users and admins and allows listing stored configuration keys (when `STATEFUL_MODE` is set to `simple`). This defaults to `no` and can be set to `yes`.
| `APPRISE_INTERPRET_EMOJIS` | Override the Apprise `interpret-emojis` setting. This defaults to `none` (not set), but can be enforced to `no` or `yes`.
| `APPRISE_DENY_SERVICES` | A comma separated set of entries identifying what plugins to deny access to. You only need to identify one schema entry associated with a plugin to in turn disable all of it.  Hence, if you wanted to disable the `glib` plugin, you do not need to additionally include `qt` as well since it's included as part of the (`dbus`) package; consequently specifying `qt` would in turn disable the `glib` module as well (another way to accomplish the same task).  To exclude/disable more the one upstream service, simply specify additional entries separated by a `,` (comma) or ` ` (space). The `APPRISE_DENY_SERVICES` entries are ignored if the `APPRISE_ALLOW_SERVICES` is identified. By default, this is initialized to `windows, dbus, gnome, macosx, syslog` (blocking local actions from being issued inside of the docker container)
| `APPRISE_ALLOW_SERVICES` | A comma separated set of entries identifying what plugins to allow access to. You may only use alpha-numeric characters as is the restriction of Apprise Schemas (schema://) anyway.  To exclusively include more the one upstream service, simply specify additional entries separated by a `,` (comma) or ` ` (space). The `APPRISE_DENY_SERVICES` entries are ignored if the `APPRISE_ALLOW_SERVICES` is identified.
| `APPRISE_API_ONLY` | Defines whether access to the web interface will be open for configuration if necessary, can be enforced to `no` or `yes`.
| `APPRISE_ATTACH_ALLOW_URLS` | A comma separated set of entries identifying the HTTP Attach URLs the Apprise API shall always accept.  Use wildcards such as `*` and `?` to help construct the URL/Hosts you identify. Use a space and/or a comma to identify more then one entry. By default this is set to `*` (Accept all provided URLs).
| `APPRISE_ATTACH_DENY_URLS` | A comma separated set of entries identifying the HTTP Attach URLs the Apprise API shall always reject.  Use wildcards such as `*` and `?` to help construct the URL/Hosts you identify. The `APPRISE_ATTACH_DENY_URLS` is always processed before the `APPRISE_ATTACH_ALLOW_URLS` list. Use a space and/or a comma to identify more then one entry. By default this is set to `127.0.* localhost*`.
| `SECRET_KEY`       | A Django variable acting as a *salt* for most things that require security. This API uses it for the hash sequences when writing the configuration files to disk (`hash` mode only).
| `ALLOWED_HOSTS`    | A list of strings representing the host/domain names that this API can serve. This is a security measure to prevent HTTP Host header attacks, which are possible even under many seemingly-safe web server configurations. By default this is set to `*` allowing any host. Use space to delimit more than one host.
| `APPRISE_PLUGIN_PATHS` | Apprise supports the ability to define your own `schema://` definitions and load them.  To read more about how you can create your own customizations, check out [this link here](https://appriseit.com/dev/decorator/). You may define one or more paths (separated by comma `,`) here. By default the `apprise_api/var/plugin` directory is scanned (which does not include anything). Feel free to set this to an empty string to disable any custom plugin loading.
| `APPRISE_RECURSION_MAX` | This defines the number of times one Apprise API Server can (recursively) call another.  This is to both support and mitigate abuse through [the `apprise://` schema](https://appriseit.com/services/apprise_api/) for those who choose to use it. When leveraged properly, you can increase this (recursion max) value and successfully load balance the handling of many notification requests through many additional API Servers.  By default this value is set to `1` (one).
| `APPRISE_WEBHOOK_URL` | Define a Webhook that Apprise should `POST` results to upon each notification call made.  This must be in the format of an `http://` or `https://` URI.  By default no URL is specified and no webhook is actioned.
| `APPRISE_WORKER_COUNT` | Over-ride the number of workers to run.  by default this is calculated `(2 * CPUS_DETECTED) + 1` [as advised by Gunicorn's website](https://docs.gunicorn.org/en/stable/design.html#how-many-workers). Hobby enthusiasts and/or users who are simply setting up Apprise to support their home (light-weight usage) may wish to set this value to `1` to limit the resources the Apprise server prepares for itself.
| `APPRISE_WORKER_TIMEOUT` | Over-ride the worker timeout value (in seconds); by default this is `300` (5 min) which should be more than enough time to send all pending notifications.
| `APPRISE_BASE_URL`    | Those who are hosting the API behind a proxy that requires a subpath to gain access to this API should specify this path here as well.  By default this is not set at all.
| `LOG_LEVEL`    | Adjust the log level to the console. Possible values are `CRITICAL`, `ERROR`, `WARNING`, `INFO`, and `DEBUG`.
| `DEBUG`            | This defaults to `no` and can however be set to `yes` by simply defining the global variable as such.


## Nginx Overrides

The 2 files you can override are:
1. `/etc/nginx/location-override.conf` which is included within all of the Apprise API NginX `location` references.
1. `/etc/nginx/server-override.conf` which is included within Apprise API `server` reference.

### Authentication
Under the hood, Apprise-API is running a small NginX instance.  It allows for you to inject your own configuration into it. One thing you may wish to add is basic authentication.

Below we create ourselves some nginx directives we'd like to apply to our Apprise API:
```nginx
# Our override.conf file:
auth_basic            "Apprise API Restricted Area";
auth_basic_user_file  /etc/nginx/.htpasswd;
```

Now let's set ourselves up with a simple password file (for more info on htpasswd files, see [here](https://docs.nginx.com/nginx/admin-guide/security-controls/configuring-http-basic-authentication/)
```bash
# Create ourselves a for our user 'foobar'; the below will prompt you for the pass
# you want to provide:
htpasswd -c apprise_api.htpasswd foobar

# Note: the -c above is only needed to create the database for the first time
```

Now we can create our docker container with this new authentication information:
```bash
# Pre-create the paths you will mount to
mkdir -p /path/to/local/{attach,config,plugin}

# Create our container containing Basic Auth:
docker run --name apprise \
   -p 8000:8000 \
   --user "$(id -u):$(id -g)" \
   -v /path/to/local/config:/config \
   -v /path/to/local/attach:/attach \
   -v /path/to/local/plugin:/plugin \
   -v ./override.conf:/etc/nginx/location-override.conf:ro \
   -v ./apprise_api.htpasswd:/etc/nginx/.htpasswd:ro \
   -e APPRISE_STATEFUL_MODE=simple \
   -e APPRISE_WORKER_COUNT=1 \
   -e APPRISE_ADMIN=y \
   -d caronc/apprise:latest
```

Visit http://localhost:8000 to see if things are working as expected. If you followed the example above, you should log in as the user `foobar` using the credentials you provided the account.

You can add further accounts to the existing database by omitting the `-c` switch:
```bash
# Add another account
htpasswd apprise_api.htpasswd user2
```

## Kubernetes

### Example Kubernetes Deployment
Thanks to @steled, here is what a potential Kubernetes deployment configuration could also look like (note that this uses the legacy PGID and PUID global variables:
```yaml
apiVersion: v1
kind: Namespace
metadata:
  labels:
    name: apprise
  name: apprise
---
apiVersion: v1
kind: ConfigMap
metadata:
  labels:
    name: apprise
  name: apprise-api-override-conf-config
  namespace: apprise
data:
  location-override.conf: |
    auth_basic            "Apprise API Restricted Area";
    auth_basic_user_file  /etc/nginx/.htpasswd;
---
apiVersion: v1
kind: Secret
metadata:
  labels:
    name: apprise
  name: apprise-api-htpasswd-secret
  namespace: apprise
data:
  .htpasswd: <base64_encoded> # add output of: htpasswd -c apprise_api.htpasswd <USERNAME> && cat apprise_api.htpasswd | base64
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  labels:
    name: apprise
  name: apprise-data
  namespace: apprise
spec:
  accessModes:
  - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
---
apiVersion: v1
kind: Service
metadata:
  labels:
    name: apprise
  name: apprise
  namespace: apprise
spec:
  ports:
  - name: http
    port: 80
    protocol: TCP
    targetPort: 8000
  selector:
    name: apprise
  type: ClusterIP
---
apiVersion: apps/v1
kind: Deployment
metadata:
  labels:
    name: apprise
  name: apprise
  namespace: apprise
spec:
  replicas: 1
  selector:
    matchLabels:
      name: apprise
  strategy:
    type: Recreate
  template:
    metadata:
      labels:
        name: apprise
    spec:
      containers:
        - env:
            - name: APPRISE_STATEFUL_MODE
              value: simple
            - name: PGID
              value: "1000"
            - name: PUID
              value: "1000"
          image: caronc/apprise:1.1
          name: apprise
          ports:
            - containerPort: 8000
              protocol: TCP
          resources:
            limits:
              cpu: "500m"
              memory: "512Mi"
            requests:
              cpu: "250m"
              memory: "128Mi"
          volumeMounts:
            - mountPath: /config
              name: apprise-data
            - mountPath: /plugin
              name: apprise-data
            - mountPath: /attach
              name: apprise-data
            # the following mountPath can be removed if not wanted/used
            - mountPath: /etc/nginx/.htpasswd
              name: apprise-api-htpasswd-secret-volume
              readOnly: true
              subPath: .htpasswd
            # the following mountPath can be removed if not wanted/used
            - mountPath: /etc/nginx/location-override.conf
              name: apprise-api-override-conf-config-volume
              readOnly: true
              subPath: location-override.conf
      restartPolicy: Always
      volumes:
        - name: apprise-data
          persistentVolumeClaim:
            claimName: apprise-data
        # the following volume can be removed if not wanted/used
        - name: apprise-api-htpasswd-secret-volume
          secret:
            secretName: apprise-api-htpasswd-secret
        # the following volume can be removed if not wanted/used
        - name: apprise-api-override-conf-config-volume
          configMap:
            name: apprise-api-override-conf-config
```

### Hardened Kubernetes deployment (rootless and read only)

For a more security conscious setup, you can run Apprise API as a non-root
user with a read only root filesystem and explicit ephemeral volumes.

The following example assumes you already created persistent volume claims
for `/config`, `/plugin`, and `/attach`.

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  labels:
    app: apprise
  name: apprise
  namespace: apprise
spec:
  replicas: 1
  selector:
    matchLabels:
      app: apprise
  template:
    metadata:
      labels:
        app: apprise
    spec:
      securityContext:
        runAsNonRoot: true
        runAsUser: 1000
        fsGroup: 1000
        readOnlyRootFilesystem: true
      containers:
        - name: apprise
          image: caronc/apprise:latest
          imagePullPolicy: IfNotPresent
          securityContext:
            allowPrivilegeEscalation: false
            capabilities:
              drop: ["ALL"]
          env:
            - name: APPRISE_STATEFUL_MODE
              value: simple
            - name: APPRISE_WORKER_COUNT
              value: "1"
            - name: APPRISE_ADMIN
              value: "y"
          ports:
            - containerPort: 8000
              name: http
          volumeMounts:
            # Persistent data
            - name: config
              mountPath: /config
            - name: plugin
              mountPath: /plugin
            - name: attach
              mountPath: /attach

            # Ephemeral runtime and temp files
            - name: tmp
              mountPath: /tmp

      volumes:
        - name: config
          persistentVolumeClaim:
            claimName: apprise-config
        - name: plugin
          persistentVolumeClaim:
            claimName: apprise-plugin
        - name: attach
          persistentVolumeClaim:
            claimName: apprise-attach

        # The deployment mounts /tmp as an in-memory emptyDir, which is where nginx,
        # gunicorn, and supervisord store pids, sockets, and temporary files.
        # This is configured as an ephemeral volume, stored in memory.
        - name: tmp
          emptyDir:
            medium: Memory
```

## Development Environment
The following should get you a working development server to test with:

### Bare Metal
```bash
# Start the development server in debug mode:
tox -e runserver
# Then visit: http://localhost:8000/

# If you want to run on a different port:
tox -e runserver -- "localhost:8080"
# Then visit: http://localhost:8000/

# You can also bind it to all of your interfaces like so:
tox -e runserver -- "0.0.0.0:8080"
```

### Docker Compose for Development
Running `docker compose up` in a fresh checkout will automatically apply `docker-compose.override.yml`.
This mounts the local source tree and static assets into the container so UI and template changes are
reflected immediately without rebuilding the image.
```bash
# Pre-create the paths you will mount to
mkdir -p attach config plugin
```

Then:
```bash
PUID=$(id -u) PGID=$(id -g) docker compose up
```

### Quality Assurance and Testing (via Tox)

The project uses `tox` to manage linting, testing, and formatting in a reproducible way.

```bash
# Run unit tests
tox -e test

# Test structure; calls ruff under the hood
tox -e lint
```

**Note**: You can combine environments, e.g.:
```bash
tox -e test,lint
```

Automatically format your code if possible to pass linting after changes:
```bash
tox -e format
```

### Manual Tools (optional)
The following also works assuming you have provided all development dependencies (`pip install .[dev]`)
```bash
# Run unit tests manually (if needed)
pytest apprise_api

# Lint code with Ruff
ruff check .

# Format code with Ruff
ruff format .
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


If you're server has the `APPRISE_CONFIG_LOCK` set, you can still leverage [the `apprise://` plugin](https://appriseit.com/services/apprise_api/) to trigger our pre-saved notifications:
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


## Metrics Collection & Analysis

Basic Prometheus support added through `/metrics` reference point.

## OpenAPI / Swagger Specification

Apprise API includes an OpenAPI 3 specification in `swagger.yaml` at the root
of the repository.

For local development you can bring up a standalone Swagger UI that reads the
checked-in spec file without changing how Apprise API runs:

```bash
docker compose -f docker-compose.swagger.yml up -d
# Then browse to:
#   http://localhost:8001
```

# Want To Learn More?

If you're interested in reading more about this and other methods on how to customize your own notifications, please check out the following links:
* 🌎 [Apprise API/Web Interface](https://github.com/caronc/apprise-api/)
* 🛠️ [Development API](https://appriseit.com/library/)
* ⚙️ [Configuration File Help](https://appriseit.com/getting-started/configuration/)
* ⚡ [Create Your Own Custom Notifications](https://appriseit.com/library/extending/decorator/)
* 📣 [Using the CLI](https://appriseit.com/cli/)
* 📖 [Apprise Documentation Source](https://github.com/caronc/apprise-docs/)
* 🔧 [Troubleshooting](https://appriseit.com/qa/)
* 🎉 [Showcase](https://appriseit.com/contributing/showcase/)

Want to help make Apprise better?
* 💡 [Contribute to the Apprise Code Base](https://appriseit.com/contributing/)
* ❤️ [Sponsorship and Donations](https://appriseit.com/contributing/sponsors/)
