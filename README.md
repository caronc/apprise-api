# Apprise API

Take advantage of [Apprise](https://github.com/caronc/apprise) through your network with a user-friendly API.

- Send notifications to more then 50+ services.
- An incredibly lightweight gateway to Apprise.
- A production ready micro-service at your disposal.

Apprise API was designed to easily fit into existing (and new) eco-systems that are looking for a simple notification solution.

[![Paypal](https://img.shields.io/badge/paypal-donate-green.svg)](https://www.paypal.com/cgi-bin/webscr?cmd=_s-xclick&hosted_button_id=MHANV39UZNQ5E)
[![Discord](https://img.shields.io/discord/558793703356104724.svg?colorB=7289DA&label=Discord&logo=Discord&logoColor=7289DA&style=flat-square)](https://discord.gg/MMPeN2D)

## Screenshots
There is a small built-in *Configuration Manager* that can be accesed using `/cfg/{KEY}`:<br/>
![Screenshot of GUI - Using Keys](https://raw.githubusercontent.com/caronc/apprise-api/master/Screenshot-1.png)<br/>

Below is a screenshot of how you can set either a series of URL's to your `{KEY}`, or set your YAML and/or TEXT configuration below.
![Screenshot of GUI - Configuration](https://raw.githubusercontent.com/caronc/apprise-api/master/Screenshot-2.png)

## API Details

| Path         | Description |
|------------- | ----------- |
| `/add/{KEY}` | Saves Apprise Configuration (or set of URLs) to the persistent store.<br/>*Parameters*<br/>:small_red_triangle: **urls**: Used to define one or more Apprise URL(s). Use a comma and/or space to separate one URL from the next.<br/>:small_red_triangle: **config**: Provide the contents of either a YAML or TEXT based Apprise configuration.<br/>:small_red_triangle: **format**: This field is only required if you've specified the _config_ parameter. Used to tell the server which of the supported (Apprise) configuration types you are passing. Valid options are _text_ and _yaml_.
| `/del/{KEY}` | Removes Apprise Configuration from the persistent store.
| `/get/{KEY}` | Returns the Apprise Configuration from the persistent store.  This can be directly used with the *Apprise CLI* and/or the *AppriseConfig()* object ([see here for details](https://github.com/caronc/apprise/wiki/config)).
| `/notify/{KEY}` | Sends a notification based on the Apprise Configuration associated with the specified *{KEY}*.<br/>*Parameters*<br/>:small_red_triangle: **body**: Your message body. This is the *only* required field.<br/>:small_red_triangle: **title**: Optionally define a title to go along with the *body*.<br/>:small_red_triangle: **type**: Defines the message type you want to send as.  The valid options are `info`, `success`, `warning`, and `error`. If no *type* is specified then `info` is the default value used.<br/>:small_red_triangle: **tag**: Optionally notify only those tagged accordingly.
| `/notify/` | Similar to the `notify` API identified above except this one sends a stateless notification and requires no reference to a `{KEY}`. <br/>*Parameters*<br/>:small_red_triangle: **urls**: One or more URLs identifying where the notification should be sent to. If this field isn't specified then it automatically assumes the `settings.APPRISE_STATELESS_URLS` value or `APPRISE_STATELESS_URLS` environment variable.<br/>:small_red_triangle: **body**: Your message body. This is a required field.<br/>:small_red_triangle: **title**: Optionally define a title to go along with the *body*.<br/>:small_red_triangle: **type**: Defines the message type you want to send as.  The valid options are `info`, `success`, `warning`, and `error`. If no *type* is specified then `info` is the default value used.

### API Notes

- `{KEY}` must be 1-64 alphanumeric characters in length. In addition to this, the underscore (`_`) and dash (`-`) are also accepted.
- You must `POST` to URLs defined above in order for them to respond.
- Specify the `Content-Type` of `application/json` to use the JSON support.
- There is no authentication required to use this API; this is by design. It's intention is to be a lightweight and fast micro-service parked behind the systems designed to handle security.
- There are no persistent store dependencies for the purpose of simplicity. Configuration is hashed and written straight to disk.

### Environment Variables

The use of environment variables allow you to provide over-rides to default settings.

| Variable             | Description |
|--------------------- | ----------- |
| `APPRISE_CONFIG_DIR` | Defines the persistent store location of all configuration files saved. By default:<br/> - Content is written to the `apprise_api/var/config` directory when just using the _Django_ `manage runserver` script.
| `APPRISE_STATELESS_URLS` | A default set of URLs to notify when using the stateless `/notify` reference (no reference to `{KEY}` variables).
| `SECRET_KEY`       | A Django variable acting as a *salt* for most things that require security. This API uses it for the hash sequences when writing the configuration files to disk.
| `ALLOWED_HOSTS`    | A list of strings representing the host/domain names that this API can serve. This is a security measure to prevent HTTP Host header attacks, which are possible even under many seemingly-safe web server configurations. By default this is set to `*` allowing any host. Use space to delimit more then one host.
| `DEBUG`            | This defaults to `False` however can be set to `True`if defined with a non-zero value (such as `1`).


## Container Support

A `docker-compose.yml` file is already set up to grant you an instant production ready simulated environment:

```bash
# Docker Compose
docker-compose up
```

You can now access the API at: `http://localhost:8000/` from your browser.

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

## Micro-Service Integration

Perhaps you run your own service and the only goal you have is to add notification support to it. Here is an example:
```bash
# Send your notifications
curl -X POST -d '{"urls": "mailto://user:pass@gmail.com", "body":"test message"}' \
    -H "Content-Type: application/json" \
    http://localhost:8000/notify
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
