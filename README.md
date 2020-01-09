# Apprise API

Take advantage of [Apprise](https://github.com/caronc/apprise) through your network with a user-friendly API.

- Send notifications to more then 50+ services.
- An incredibly lightweight gateway to Apprise.
- A production ready micro-service at your disposal.

Apprise API was designed to easily fit into existing (and new) eco-systems that are looking for a simple notification solution.

[![Paypal](https://img.shields.io/badge/paypal-donate-green.svg)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=MHANV39UZNQ5E)
[![Follow](https://img.shields.io/twitter/follow/l2gnux)](https://twitter.com/l2gnux/)<br/>
[![Discord](https://img.shields.io/discord/558793703356104724.svg?colorB=7289DA&label=Discord&logo=Discord&logoColor=7289DA&style=flat-square)](https://discord.gg/MMPeN2D)
[![Build Status](https://travis-ci.org/caronc/apprise-api.svg?branch=master)](https://travis-ci.org/caronc/apprise-api)
[![CodeCov Status](https://codecov.io/github/caronc/apprise-api/branch/master/graph/badge.svg)](https://codecov.io/github/caronc/apprise-api)
[![Docker Pulls](https://img.shields.io/docker/pulls/caronc/apprise.svg?style=flat-square)](https://hub.docker.com/r/caronc/apprise)

## Screenshots
There is a small built-in *Configuration Manager* that can be optionally accessed through your web browser accessible via `/cfg/{KEY}`:<br/>
![Screenshot of GUI - Using Keys](https://raw.githubusercontent.com/caronc/apprise-api/master/Screenshot-1.png)<br/>

Below is a screenshot of how you can set either a series of URL's to your `{KEY}`, or set your YAML and/or TEXT configuration below.
![Screenshot of GUI - Configuration](https://raw.githubusercontent.com/caronc/apprise-api/master/Screenshot-2.png)

## Installation
The following options should allow you to access the API at: `http://localhost:8000/` from your browser.

Using [dockerhub](https://hub.docker.com/r/caronc/apprise), you can do the following:
```bash
# Retrieve container
docker pull caronc/apprise:latest

# Start it up:
# /config is used for a persistent store, you do not have to mount
#         this if you don't intend to use it.
docker run --name apprise \
   -p 8000:8000 \
   -v /var/lib/apprise/config:/config \
   -d caronc/apprise:latest
```

A `docker-compose.yml` file is already set up to grant you an instant production ready simulated environment:

```bash
# Docker Compose
docker-compose up
```

## API Details

| Path         | Method | Description |
|------------- | ------ | ----------- |
| `/add/{KEY}` |  POST  | Saves Apprise Configuration (or set of URLs) to the persistent store.<br/>*Parameters*<br/>:small_red_triangle: **urls**: Used to define one or more Apprise URL(s). Use a comma and/or space to separate one URL from the next.<br/>:small_red_triangle: **config**: Provide the contents of either a YAML or TEXT based Apprise configuration.<br/>:small_red_triangle: **format**: This field is only required if you've specified the _config_ parameter. Used to tell the server which of the supported (Apprise) configuration types you are passing. Valid options are _text_ and _yaml_.
| `/del/{KEY}` |  POST  | Removes Apprise Configuration from the persistent store.
| `/get/{KEY}` |  POST  |  Returns the Apprise Configuration from the persistent store.  This can be directly used with the *Apprise CLI* and/or the *AppriseConfig()* object ([see here for details](https://github.com/caronc/apprise/wiki/config)).
| `/notify/{KEY}` |  POST  |  Sends a notification based on the Apprise Configuration associated with the specified *{KEY}*.<br/>*Parameters*<br/>:small_red_triangle: **body**: Your message body. This is the *only* required field.<br/>:small_red_triangle: **title**: Optionally define a title to go along with the *body*.<br/>:small_red_triangle: **type**: Defines the message type you want to send as.  The valid options are `info`, `success`, `warning`, and `error`. If no *type* is specified then `info` is the default value used.<br/>:small_red_triangle: **tag**: Optionally notify only those tagged accordingly.
| `/notify/` |  POST  | Similar to the `notify` API identified above except this one sends a stateless notification and requires no reference to a `{KEY}`. <br/>*Parameters*<br/>:small_red_triangle: **urls**: One or more URLs identifying where the notification should be sent to. If this field isn't specified then it automatically assumes the `settings.APPRISE_STATELESS_URLS` value or `APPRISE_STATELESS_URLS` environment variable.<br/>:small_red_triangle: **body**: Your message body. This is a required field.<br/>:small_red_triangle: **title**: Optionally define a title to go along with the *body*.<br/>:small_red_triangle: **type**: Defines the message type you want to send as.  The valid options are `info`, `success`, `warning`, and `error`. If no *type* is specified then `info` is the default value used.
| `/json/urls/{KEY}` |  GET  | Returns a JSON response object that contains all of the URLS and Tags associated with the key specified.

The `/json/urls/{KEY}` response might look like this:
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

### API Notes

- `{KEY}` must be 1-64 alphanumeric characters in length. In addition to this, the underscore (`_`) and dash (`-`) are also accepted.
- Specify the `Content-Type` of `application/json` to use the JSON support.
- There is no authentication (or SSL encryption) required to use this API; this is by design. The intentio here to be a lightweight and fast micro-service that can be parked behind another tier that was designed to handle security.
- There are no additional dependencies should you choose to use the optional persistent store (mounted as `/config`).

### Environment Variables

The use of environment variables allow you to provide over-rides to default settings.

| Variable             | Description |
|--------------------- | ----------- |
| `APPRISE_CONFIG_DIR` | Defines an (optional) persistent store location of all configuration files saved. By default:<br/> - Configuration is written to the `apprise_api/var/config` directory when just using the _Django_ `manage runserver` script. However for the path for containers is `/config`
| `APPRISE_STATELESS_URLS` | For a non-persistent solution, you can take avantage of this global variable. Use this to define a default set of Apprise URLs to notify when using API calls to `/notify`.  If no `{KEY}` is defined when calling `/notify` then the URLs defined here are used instead.
| `SECRET_KEY`       | A Django variable acting as a *salt* for most things that require security. This API uses it for the hash sequences when writing the configuration files to disk.
| `ALLOWED_HOSTS`    | A list of strings representing the host/domain names that this API can serve. This is a security measure to prevent HTTP Host header attacks, which are possible even under many seemingly-safe web server configurations. By default this is set to `*` allowing any host. Use space to delimit more then one host.
| `DEBUG`            | This defaults to `False` however can be set to `True`if defined with a non-zero value (such as `1`).


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

# Run a dev server (debug mode):
./manage.py runserver
```

You can now access the API at: `http://localhost:8000/` from your browser.

Some other useful development notes:

```bash
# Check for any lint errors
flake8 apprise_api

# Run unit tests
pytest apprise_api
```

## Apprise Integration

### Apprise CLI Pull Example

A scenario where you want to poll the API for your configuration:

```bash
# A simple example of the Apprise CLI
# pulling down previously stored configuration
apprise -body="test message" --config=http://localhost:8000/get/{KEY}
```

### AppriseConfig() Pull Example

Using the Apprise Library through Python, you can easily pull your saved configuration off of the API to use for future notifications.

```python
import apprise

# Point our configuration to this API server:
config = apprise.AppriseConfig()
config.add('http://localhost:8000/get/{KEY}')

# Create our Apprise Instance
a = apprise.Apprise()

# Store our new configuration
a.add(config)

# Send a test message
a.notify('test message')
```

## Micro-Service Integration
You can trigger the API to notify your pre-created keys like so: (using `/notify/{key}`):
```bash
# Send notification(s) to a {key} defined as 'my-apprise-key'
curl -X POST -d '{"body":"test message"}' \
    -H "Content-Type: application/json" \
    http://localhost:8000/notify/my-apprise-key
```

Here is a *stateless* example where no key is required (using `/notify/`):
```bash
# Send your notifications directly
curl -X POST -d '{"urls": "mailto://user:pass@gmail.com", "body":"test message"}' \
    -H "Content-Type: application/json" \
    http://localhost:8000/notify
```
