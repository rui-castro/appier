#!/usr/bin/python
# -*- coding: utf-8 -*-

# Hive Appier Framework
# Copyright (C) 2008-2012 Hive Solutions Lda.
#
# This file is part of Hive Appier Framework.
#
# Hive Appier Framework is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# Hive Appier Framework is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Hive Appier Framework. If not, see <http://www.gnu.org/licenses/>.

__author__ = "João Magalhães joamag@hive.pt>"
""" The author(s) of the module """

__version__ = "1.0.0"
""" The version of the module """

__revision__ = "$LastChangedRevision$"
""" The revision number of the module """

__date__ = "$LastChangedDate$"
""" The last change date of the module """

__copyright__ = "Copyright (c) 2008-2012 Hive Solutions Lda."
""" The copyright for the module """

__license__ = "GNU General Public License (GPL), Version 3"
""" The license for the module """

import os
import re
import sys
import imp
import json
import types
import locale
import urllib2
import inspect
import urlparse
import datetime
import mimetypes
import threading
import traceback

import logging.handlers

import log
import http
import util
import smtp
import async
import model
import mongo
import config
import request
import defines
import settings
import observer
import controller
import exceptions

APP = None
""" The global reference to the application object this
should be a singleton object and so no multiple instances
of an app may exist in the same process """

NAME = "appier"
""" The name to be used to describe the framework while working
on its own environment, this is just a descriptive value """

VERSION = "0.4.1"
""" The version of the framework that is currently installed
this value may be used for debugging/diagnostic purposes """

API_VERSION = 1
""" The incremental version number that may be used to
check on the level of compatibility for the api """

BUFFER_SIZE = 4096
""" The size of the buffer so be used while sending data using
the static file serving approach (important for performance) """

MAX_LOG_SIZE = 524288
""" The maximum amount of bytes for a log file created by
the rotating file handler, after this value is reached a
new file is created for the buffering of the results """

MAX_LOG_COUNT = 5
""" The maximum number of files stores as backups for the
rotating file handler, note that these values are stored
just for extra debugging purposes """

RUNNING = "running"
""" The running state for the app, indicating that the
complete api set is being served correctly """

STOPPED = "stopped"
""" The stopped state for the app, indicating that some
of the api components may be down """

REPLACE_REGEX = re.compile("(?<!\(\?P)\<((\w+):)?(\w+)\>")
""" The regular expression to be used in the replacement
of the capture groups for the urls, this regex will capture
any named group not change until this stage (eg: int,
string, regex, etc.) """

INT_REGEX = re.compile("\<int:(\w+)\>")
""" The regular expression to be used in the replacement
of the integer type based groups for the urls """

ESCAPE_EXTENSIONS = (
    ".xml",
    ".html",
    ".xhtml",
    ".liquid",
    ".xml.tpl",
    ".html.tpl",
    ".xhtml.tpl"
)
""" The sequence containing the various extensions
for which the autoescape mode will be enabled  by
default as expected by the end developer """

TYPES_R = dict(
    int = int,
    str = unicode
)
""" Map that resolves a data type from the string representation
to the proper type value to be used in casting """

EXCLUDED_NAMES = (
    "server",
    "host",
    "port",
    "ssl",
    "key_file",
    "cer_file"
)
""" The sequence that contains the names that are considered
excluded from the auto parsing of parameters """

BASE_HEADERS = (
    ("X-Powered-By", "%s/%s" % (NAME, VERSION)),
)
""" The sequence containing the headers considered to be basic
and that are going to be applied to all of the requests received
by the appier framework (water marking each of them) """

REQUEST_LOCK = threading.RLock()
""" The lock to be used in the application handling of request
so that no two request get handled at the same time for the current
app instance, as that would create some serious problems """

class App(observer.Observable):
    """
    The base application object that should be inherited
    from all the application in the appier environment.
    This object is responsible for the starting of all the
    structures and for the routing of the request.
    It should also be compliant with the WSGI specification.
    """

    _BASE_ROUTES = []
    """ Set of routes meant to be enable in a static
    environment using for instance decorators this is
    required because at the time of application loading
    there's no application instance available """

    _ERROR_HANDLERS = {}
    """ The dictionary associating the error object (may be
    both an integer code or an exception class) with the
    proper method that is going to be used to handle that
    error when it is raised """

    def __init__(
        self,
        name = None,
        locales = ("en_us",),
        parts = (),
        handlers = None,
        service = True,
        safe = False,
        offset = 2
    ):
        observer.Observable.__init__(self)
        self.name = name or self.__class__.__name__
        self.locales = locales
        self.parts = parts
        self.service = service
        self.safe = safe
        self.server = None
        self.host = None
        self.port = None
        self.ssl = False
        self.manager = async.SimpleManager()
        self.routes_v = None
        self.type = "default"
        self.status = STOPPED
        self.start_date = None
        self.cache = datetime.timedelta(days = 1)
        self.part_routes = []
        self.context = {}
        self.controllers = {}
        self.names = {}
        self._set_global()
        self._load_paths(offset)
        self._load_config()
        self._load_settings()
        self._load_logging()
        self._load_handlers(handlers)
        self._load_context()
        self._load_bundles()
        self._load_controllers()
        self._load_models()
        self._load_parts()
        self._load_templating()
        self._load_patches()
        self._set_config()

    def __getattr__(self, name):
        if not name in ("session",):
            raise AttributeError("'%s' not found" % name)

        if not hasattr(self, "request"):
            raise AttributeError("'%s' not found" % name)

        if not hasattr(self.request, name):
            raise AttributeError("'%s' not found" % name)

        return getattr(self.request, name)

    @staticmethod
    def load():
        logging.basicConfig(format = log.LOGGING_FORMAT)

    @staticmethod
    def add_route(method, expression, function, async = False, json = False, context = None):
        # creates the list that will hold the various parameters (type and
        # name tuples) and the map that will map the name of the argument
        # to the string representing the original expression of it so that
        # it may be latter used for reference (as specified in definition)
        param_t = []
        names_t = {}

        # retrieves the data type of the provided method and in case it
        # references a string type converts it into a simple tuple otherwise
        # uses it directly, then creates the options dictionary with the
        # series of values that are going to be used as options in the route
        method_t = type(method)
        method = (method,) if method_t in types.StringTypes else method
        opts = dict(
            json = json,
            async = async,
            base = expression,
            param_t = param_t,
            names_t = names_t,
        )

        # creates a new match based iterator to try to find all the parameter
        # references in the provided expression so that meta information may
        # be created on them to be used latter in replacements
        iterator = REPLACE_REGEX.finditer(expression)
        for match in iterator:
            # retrieves the group information on the various groups and unpacks
            # them creating the param tuple from the resolved type and the name
            # of the parameter (to be used in parameter passing casting)
            _type_s, type_t, name = match.groups()
            type_r = TYPES_R.get(type_t, str)
            param = (type_r, name)

            # creates the target (replacement) expression taking into account if
            # the type values has been provided or not
            if type_t: target = "<" + type_t + ":" + name + ">"
            else: target = "<" + name + ">"

            # adds the parameter to the list of parameter tuples and then sets the
            # target replacement association (name to target string)
            param_t.append(param)
            names_t[name] = target

        # runs the regex based replacement chain that should translate
        # the expression from a simplified domain into a regex based domain
        # that may be correctly compiled into the rest environment then
        # creates the route list, compiling the expression and ads the route
        # to the list of routes for the current global application
        expression = "^" + expression + "$"
        expression = INT_REGEX.sub(r"(?P[\1>[\d]+)", expression)
        expression = REPLACE_REGEX.sub(r"(?P[\3>[\:\.\s\w-]+)", expression)
        expression = expression.replace("?P[", "?P<")
        route = [method, re.compile(expression, re.UNICODE), function, context, opts]
        App._BASE_ROUTES.append(route)

    @staticmethod
    def add_error(error, method, context = None):
        App._ERROR_HANDLERS[error] = [method, context]

    @staticmethod
    def add_exception(exception, method, context = None):
        App._ERROR_HANDLERS[exception] = [method, context]

    def start(self):
        if self.status == RUNNING: return
        self.start_date = datetime.datetime.utcnow()
        if self.manager: self.manager.start()
        self.status = RUNNING

    def stop(self):
        if self.status == STOPPED: return
        self.status = STOPPED

    def serve(
        self,
        server = "netius",
        host = "127.0.0.1",
        port = 8080,
        ssl = False,
        key_file = None,
        cer_file = None,
        **kwargs
    ):
        server = config.conf("SERVER", server)
        host = config.conf("HOST", host)
        port = config.conf("PORT", port, cast = int)
        ssl = config.conf("SSL", ssl, cast = bool)
        key_file = config.conf("KEY_FILE", key_file)
        cer_file = config.conf("CER_FILE", cer_file)
        servers = config.conf_prefix("SERVER_")
        for name, value in servers.iteritems():
            name_s = name.lower()[7:]
            if name_s in EXCLUDED_NAMES: continue
            kwargs[name_s] = value
        kwargs["handlers"] = self.handlers
        kwargs["level"] = self.level
        self.logger.info("Starting '%s' with '%s'..." % (self.name, server))
        self.server = server; self.host = host; self.port = port; self.ssl = ssl
        self.start()
        method = getattr(self, "serve_" + server)
        names = method.func_code.co_varnames
        if "ssl" in names: kwargs["ssl"] = ssl
        if "key_file" in names: kwargs["key_file"] = key_file
        if "cer_file" in names: kwargs["cer_file"] = cer_file
        try: return_value = method(host = host, port = port, **kwargs)
        except BaseException, exception:
            lines = traceback.format_exc().splitlines()
            self.logger.critical("Unhandled exception received: %s" % unicode(exception))
            for line in lines: self.logger.warning(line)
            raise
        self.stop()
        self.logger.info("Stopped '%s'' in '%s' ..." % (self.name, server))
        return return_value

    def serve_waitress(self, host, port, **kwargs):
        """
        Starts the serving of the current application using the
        python based waitress server in the provided host and
        port as requested.

        For more information on the waitress http server please
        refer to https://pypi.python.org/pypi/waitress.

        @type host: String
        @param host: The host name of ip address to bind the server
        to, this value should be represented as a string.
        @type port: int
        @param port: The tcp port for the bind operation of the
        server (listening operation).
        """

        import waitress
        waitress.serve(self.application, host = host, port = port)

    def serve_netius(self, host, port, ssl = False, key_file = None, cer_file = None, **kwargs):
        """
        Starts serving the current application using the hive solutions
        python based web server netius http, this is supposed to be used
        with care as the server is still under development.

        For more information on the netius http servers please refer
        to the https://bitbucket.org/hivesolutions/netius site.

        @type host: String
        @param host: The host name of ip address to bind the server
        to, this value should be represented as a string.
        @type port: int
        @param port: The tcp port for the bind operation of the
        server (listening operation).
        @type ssl: bool
        @param ssl: If the ssl framework for encryption should be used
        in the creation of the server socket.
        @type key_file: String
        @param key_file: The path to the file containing the private key
        that is going to be used in the ssl communication.
        @type cer_file: String
        @param cer_file: The path to the certificate file to be used in
        the ssl based communication.
        """

        import netius.servers
        server = netius.servers.WSGIServer(self.application, **kwargs)
        server.serve(
            host = host,
            port = port,
            ssl = ssl,
            key_file = key_file,
            cer_file = cer_file
        )

    def serve_tornado(self, host, port, ssl = False, key_file = None, cer_file = None, **kwargs):
        import tornado.wsgi
        import tornado.httpserver

        ssl_options = ssl and dict(
            keyfile = key_file,
            certfile = cer_file
        ) or None

        container = tornado.wsgi.WSGIContainer(self.application)
        server = tornado.httpserver.HTTPServer(container, ssl_options = ssl_options)
        server.listen(port, address = host)
        instance = tornado.ioloop.IOLoop.instance()
        instance.start()

    def serve_cherry(self, host, port, **kwargs):
        import cherrypy.wsgiserver

        server = cherrypy.wsgiserver.CherryPyWSGIServer(
            (host, port),
            self.application
        )
        try: server.start()
        except KeyboardInterrupt: server.stop()

    def load_jinja(self, **kwargs):
        try: import jinja2
        except: self.jinja = None; return

        loader = jinja2.FileSystemLoader(self.templates_path)
        self.jinja = jinja2.Environment(loader = loader)

        self.add_filter(self.to_locale, "locale")
        self.add_filter(self.nl_to_br_jinja, "nl_to_br", context = True)

        self.add_filter(self.echo, "handle")
        self.add_filter(self.script_tag_jinja, "script_tag", context = True)
        self.add_filter(self.css_tag_jinja, "css_tag", context = True)
        self.add_filter(self.css_tag_jinja, "stylesheet_tag", context = True)
        self.add_filter(self.asset_url, "asset_url")

    def add_filter(self, method, name = None, context = False):
        """
        Adds a filter to the current context in the various template
        handlers that support this kind of operation.

        Note that a call to this method may not have any behavior in
        case the handler does not support filters.

        @type method: Method
        @param method: The method that is going to be added as the
        filter handler, by default the method name is used as the name
        for the filter.
        @type name: String
        @param name: The optional name to be used as the filter name
        this is the name to be used in the template.
        @type context: bool
        @param context: If the filter to be added should have the current
        template context passed as argument.
        """

        name = name or method.__name__
        if context: method.im_func.evalcontextfilter = True
        self.jinja.filters[name] = method

    def close(self):
        pass

    def routes(self):
        base_routes = [
            (("GET",), re.compile("^/static/.*$"), self.static),
            (("GET",), re.compile("^/appier/static/.*$"), self.static_res)
        ]
        extra_routes = [
            (("GET",), re.compile("^/$"), self.info),
            (("GET",), re.compile("^/favicon.ico$"), self.icon),
            (("GET",), re.compile("^/info$"), self.info),
            (("GET",), re.compile("^/version$"), self.version),
            (("GET",), re.compile("^/log$"), self.logging),
            (("GET",), re.compile("^/debug$"), self.debug),
            (("GET", "POST"), re.compile("^/login$"), self.login),
            (("GET", "POST"), re.compile("^/logout$"), self.logout)
        ] if self.service else []
        return App._BASE_ROUTES + self.part_routes + base_routes + extra_routes

    def application(self, environ, start_response):
        REQUEST_LOCK.acquire()
        try: return self.application_l(environ, start_response)
        finally: REQUEST_LOCK.release()

    def application_l(self, environ, start_response):
        # unpacks the various fields provided by the wsgi layer
        # in order to use them in the current request handling
        method = environ["REQUEST_METHOD"]
        path = environ["PATH_INFO"]
        query = environ["QUERY_STRING"]
        script_name = environ["SCRIPT_NAME"]
        input = environ.get("wsgi.input")

        # creates the proper prefix value for the request from
        # the script name field and taking into account that this
        # value may be an empty or invalid value
        prefix = script_name if script_name.endswith("/") else script_name + "/"

        # creates the initial request object to be used in the
        # handling of the data has been received not that this
        # request object is still transient as it does not have
        # either the params and the json data set in it
        self.request = request.Request(
            method,
            path,
            prefix = prefix,
            environ = environ
        )

        # parses the provided query string creating a map of
        # parameters that will be used in the request handling
        # and then sets it in the request
        params = urlparse.parse_qs(query, keep_blank_values = True)
        params = util.decode_params(params)
        self.request.set_params(params)

        # reads the data from the input stream file and then tries
        # to load the data appropriately handling all normal cases
        # (eg json, form data, etc.)
        data = input.read()
        self.request.set_data(data)
        self.request.load_data()
        self.request.load_form()
        self.request.load_session()
        self.request.load_headers()
        self.request.load_locale(self.locales)

        # resolves the secret based params so that their content
        # is correctly decrypted according to the currently set secret
        self.request.resolve_params()

        # sets the global (operative system) locale for according to the
        # current value of the request, this value should be set while
        # the request is being handled after that it should be restored
        # back to the original (unset) value
        self._set_locale()

        # calls the before request handler method, indicating that the
        # request is going to be handled in the next few logic steps
        self.before_request()

        try:
            # handles the currently defined request and in case there's an
            # exception triggered by the underlying action methods, handles
            # it with the proper error handler so that a proper result value
            # is returned indicating the exception
            result = self.handle()

            # "extracts" the data type for the result value coming from the handle
            # method, in case the value is a generator extracts the first value from
            # it so that it may be used  for length evaluation (protocol definition)
            # at this stage it's possible to have an exception raised for a non
            # existent file or any other pre validation based problem
            result_t = type(result)
            is_generator = result_t == types.GeneratorType
            if is_generator: first = result.next()
            else: first = None
        except BaseException, exception:
            # resets the values associated with the generator based strategy so
            # that the error/exception is handled in the proper (non generator)
            # way and no interference exists for such situation, otherwise some
            # compatibility problems would occur
            is_generator = False
            first = None

            # handles the raised exception with the proper behavior so that the
            # resulting value represents the exception with either a map or a
            # string based value (properly encoded with the default encoding)
            result = self.handle_error(exception)
            self.log_error(exception)
        finally:
            # performs the flush operation in the request so that all the
            # stream oriented operation are completely performed, this should
            # include things like session flushing (into cookie)
            self.request.flush()

            # resets the locale so that the value gets restored to the original
            # value as it is expected by the current systems behavior, note that
            # this is only done in case the safe flag is active (would create some
            # serious performance problems otherwise)
            if self.safe: self._reset_locale()

        # re-retrieves the data type for the result value, this is required
        # as it may have been changed by an exception handling, failing to do
        # this would create series handling problems (stalled connection)
        result_t = type(result)

        # verifies that the type of the result is a dictionary and in
        # that's the case the success result is set in it in case not
        # value has been set in the result field
        is_map = result_t == types.DictType
        is_list = result_t in (types.ListType, types.TupleType)
        if is_map and not "result" in result: result["result"] = "success"

        # retrieves the complete set of warning "posted" during the handling
        # of the current request and in case thre's at least one warning message
        # contained in it sets the warnings in the result
        warnings = self.request.get_warnings()
        if is_map and warnings: result["warnings"] = warnings

        # retrieves any pending set cookie directive from the request and
        # uses it to update the set cookie header if it exists
        set_cookie = self.request.get_set_cookie()
        if set_cookie: self.request.set_header("Set-Cookie", set_cookie)

        # verifies if the current response is meant to be serialized as a json message
        # this is the case for both the map type of response and the list type type
        # of response as both of them represent a json message to be serialized
        is_json = is_map or is_list

        # retrieves the name of the encoding that is going to be used in case the
        # the resulting data need to be converted from unicode
        encoding = self.request.get_encoding()

        # dumps the result using the json serializer and retrieves the resulting
        # string value from it as the final message to be sent to the client, then
        # validates that the value is a string value in case it's not casts it as
        # a string using the default "serializer" structure
        result_s = mongo.dumps(result) if is_json else result
        result_t = type(result_s)
        if result_t == types.UnicodeType: result_s = result_s.encode(encoding)
        if not result_t in types.StringTypes: result_s = str(result_s)

        # calculates the final size of the resulting message in bytes so that
        # it may be used in the content length header, note that a different
        # approach is taken when the returned value is a generator, where it's
        # expected that the first yield result is the total size of the message
        result_l = first if is_generator else len(result_s)
        result_l = str(result_l)

        # sets the "target" content type taking into account the if the value is
        # set and if the current structure is a map or not
        default_content_type = is_json and "application/json" or "text/plain"
        self.request.default_content_type(default_content_type)

        # calls the after request handler that is meant to defined the end of the
        # processing of the request, this creates an extension point for final
        # modifications on the request/response to be sent to the client
        self.after_request()

        # retrieves the (output) headers defined in the current request and extends
        # them with the current content type (json) then calls starts the response
        # method so that the initial header is set to the client
        headers = self.request.get_headers() or []
        content_type = self.request.get_content_type() or "text/plain"
        code_s = self.request.get_code_s()
        headers.extend([
            ("Content-Type", content_type),
            ("Content-Length", result_l)
        ])
        headers.extend(BASE_HEADERS)
        start_response(code_s, headers)

        # determines the proper result value to be returned to the wsgi infra-structure
        # in case the current result object is a generator it's returned to the caller
        # method, otherwise a tuple is created containing the result string
        result = result if is_generator else (result_s,)
        return result

    def handle(self):
        # retrieves the current registered routes, should perform a loading only
        # on the first execution and then runs the routing process using the
        # currently set request object, retrieving the result
        routes = self._routes()
        result = self.route(routes)

        # returns the result defaulting to an empty map in case no value was
        # returned from the handling method (fallback strategy) note that this
        # strategy is only applied in case the request is considered to be a
        # success one otherwise an empty result is returned instead
        default = {} if self.request.is_success() else ""
        result = default if result == None else result
        return result

    def handle_error(self, exception):
        # formats the various lines contained in the exception and then tries
        # to retrieve the most information possible about the exception so that
        # the returned map is the most verbose as possible (as expected)
        lines = traceback.format_exc().splitlines()
        message = hasattr(exception, "message") and\
            exception.message or str(exception)
        code = hasattr(exception, "error_code") and\
            exception.error_code or 500
        errors = hasattr(exception, "errors") and\
            exception.errors or None
        session = self.request.session
        sid = session and session.sid

        # run the on error processor in the base application object and in case
        # a value is returned by a possible handler it is used as the response
        # for the current request (instead of the normal handler)
        result = self.call_error(exception, code = code)
        if result: return result

        # creates the resulting dictionary object that contains the various items
        # that are meant to describe the error/exception that has just been raised
        result = dict(
            result = "error",
            name =  exception.__class__.__name__,
            message = message,
            code = code,
            traceback =  lines,
            session = sid
        )
        if errors: result["errors"] = errors
        self.request.set_code(code)
        if not settings.DEBUG: del result["traceback"]

        # returns the resulting map to the caller method so that it may be used
        # to serialize the response in the upper layers
        return result

    def log_error(self, exception):
        # formats the various lines contained in the exception so that the may
        # be logged in the currently defined logger object
        lines = traceback.format_exc().splitlines()

        # print a logging message about the error that has just been "logged"
        # for the current request handling (logging also the traceback lines)
        self.logger.error("Problem handling request: %s" % str(exception))
        for line in lines: self.logger.warning(line)

    def call_error(self, exception, code = None):
        handler = self._ERROR_HANDLERS.get(code, None)
        if not handler: return None
        method, _name = handler
        if method: return method(exception)
        return None

    def route(self, items):
        # unpacks the various element from the request, this values are
        # going to be used along the routing process
        method = self.request.method
        path = self.request.path
        params = self.request.params
        data_j = self.request.data_j

        # runs the unquoting of the path as this is required for a proper
        # routing of the request (extra values must be correctly processed)
        # note that the value is converted into an unicode string suing the
        # proper encoding as defined by the http standard
        path_u = util.unquote(path)

        # retrieves both the callback and the mid parameters these values
        # are going to be used in case the request is handled asynchronously
        callback = params.get("callback", None)
        mid = params.get("mid", None)

        # retrieves the mid (message identifier) and the callback url from
        # the provided list of parameters in case they are defined
        mid = mid[0] if mid else None
        callback = callback[0] if callback else None

        # iterates over the complete set of routing items that are
        # going to be verified for matching (complete regex collision)
        # and runs the match operation, handling the request with the
        # proper action method associated
        for item in items:
            # unpacks the current item into the http method, regex and
            # action method and then tries to match the current path
            # against the current regex in case there's a valid match and
            # the current method is valid in the current item continues
            # the current logic (method handing)
            methods_i, regex_i, method_i = item[:3]
            match = regex_i.match(path_u)
            if not method in methods_i or not match: continue

            # verifies if there's a definition of an options map for the current
            # routes in case there's not defines an empty one (fallback)
            item_l = len(item)
            opts_i = item[3] if item_l > 3 else {}

            # tries to retrieve the payload attribute for the current item in case
            # a json data value is defined otherwise default to single value (simple
            # message handling)
            if data_j: payload = data_j["payload"] if "payload" in data_j else [data_j]
            else: payload = [data_j]

            # retrieves the number of messages to be processed in the current context
            # this value will have the same number as the callbacks calls for the async
            # type of message processing (as defined under specification)
            mcount = len(payload)

            # sets the initial (default) return value from the action method as unset,
            # this value should be overriden by the various actions methods
            return_v = None

            # updates the value of the json (serializable) request taking into account
            # the value of the json option for the request to be handled, this value
            # will be used in the serialization of errors so that the error gets properly
            # serialized even in template based events (forced serialization)
            self.request.json = opts_i.get("json", False)

            # tries to retrieve the parameters tuple from the options in the item in
            # case it does not exists defaults to an empty list (as defined in spec)
            param_t = opts_i.get("param_t", [])

            # iterates over all the items in the payload to handle them in sequence
            # as defined in the payload list (first come, first served)
            for payload_i in payload:
                # retrieves the method specification for both the "unnamed" arguments and
                # the named ones (keyword based) so that they may be used to send the correct
                # parameters to the action methods
                method_a = inspect.getargspec(method_i)[0]
                method_kw = inspect.getargspec(method_i)[2]

                # retrieves the various matching groups for the regex and uses them as the first
                # arguments to be sent to the method then adds the json data to it, after that
                # the keyword arguments are "calculated" using the provided "get" parameters but
                # filtering the ones that are not defined in the method signature
                groups = match.groups()
                groups = [value_t(value) for value, (value_t, _value_n) in zip(groups, param_t)]
                args = list(groups) + ([payload_i] if not payload_i == None else [])
                kwargs = dict([(key, value[0]) for key, value in params.iteritems() if key in method_a or method_kw])

                # in case the current route is meant to be as handled asynchronously
                # runs the logic so that the return is immediate and the handling is
                # deferred to a different thread execution logic
                is_async = opts_i.get("async", False)
                if is_async:
                    mid = self.run_async(
                        method_i,
                        callback,
                        mid = mid,
                        args = args,
                        kwargs = kwargs
                    )
                    return_v = dict(
                        result = "async",
                        mid = mid,
                        mcount = mcount
                    )
                # otherwise the request is synchronous and should be handled immediately
                # in the current workflow logic, thread execution may block for a while
                else: return_v = method_i(*args, **kwargs)

            # returns the currently defined return value, for situations where
            # multiple call have been handled this value may contain only the
            # result from the last call
            return return_v

        # raises a runtime error as if the control flow as reached this place
        # no regular expression/method association has been matched
        raise exceptions.NotFoundError(
            message = "Request %s '%s' not handled" % (method, path_u),
            error_code = 404
        )

    def run_async(self, method, callback, mid = None, args = [], kwargs = {}):
        # generates a new token to be used as the message identifier in case
        # the mid was not passed to the method (generated on client side)
        # this identifier should represent a request uniquely (nonce value)
        mid = mid or util.gen_token()

        def async_method(*args, **kwargs):
            # calls the proper method reference (base object) with the provided
            # arguments and keyword based arguments, in case an exception occurs
            # while handling the request the error should be properly serialized
            # suing the proper error handler method for the exception
            try: result = method(*args, **kwargs)
            except BaseException, exception:
                result = self.handle_error(exception)

            # verifies if a result dictionary has been created and creates a new
            # one in case it has not, then verifies if the result value is set
            # in the result if not sets it as success (fallback value)
            result = result or dict()
            if not "result" in result: result["result"] = "success"

            try:
                # in case the callback url is defined sends a post request to
                # the callback url containing the result as the json based payload
                # this value should with the result for the operation
                callback and http.post(callback, data_j = result, params = {
                    "mid" : mid
                })
            except urllib2.HTTPError, error:
                data = error.read()
                try:
                    data_s = json.loads(data)
                    message = data_s.get("message", "")
                    lines = data_s.get("traceback", [])
                except:
                    message = data
                    lines = []

                # logs the information about the callback call error, this should
                # include both the main message description but also the complete
                # set of traceback lines for the handling
                self.logger.warning("Assync callback (remote) error: %s" % message)
                for line in lines: self.logger.info(line)

        # in case no queueing manager is defined it's not possible to queue
        # the current request and so an error must be raised indicating the
        # problem that has just occurred (as expected)
        if not self.manager:
            raise exceptions.OperationalError(message = "No queue manager defined")

        # adds the current async method and request to the queue manager this
        # method will be called latter, notice that the mid is passed to the
        # manager as this is required for a proper insertion of work
        self.manager.add(mid, async_method, self.request, args, kwargs)
        return mid

    def before_request(self):
        pass

    def after_request(self):
        self._anotate_async()

    def warning(self, message):
        self.request.warning(message)

    def redirect(self, url, code = 303, **kwargs):
        query = http._urlencode(kwargs)
        if query: url += "?" + query
        self.request.code = code
        self.request.set_header("Location", url)

    def email(
        self,
        template,
        sender = None,
        receivers = [],
        subject = "",
        plain_template = None,
        host = None,
        port = None,
        username = None,
        password = None,
        stls = False,
        encoding = "utf-8",
        convert = True,
        headers = {},
        **kwargs
    ):
        host = host or config.conf("SMTP_HOST", None)
        port = port or config.conf("SMTP_PORT", 25, cast = int)
        username = username or config.conf("SMTP_USER", None)
        password = password or config.conf("SMTP_PASSWORD", None)
        stls = password or config.conf("SMTP_STARTTLS", True, cast = int)
        stls = True if stls else False

        sender_base = util.email_base(sender)
        receivers_base = util.email_base(receivers)

        sender_mime = util.email_mime(sender)
        receivers_mime = util.email_mime(receivers)

        html = self.template(template, detached = True, **kwargs)
        if plain_template: plain = self.template(plain_template, detached = True, **kwargs)
        elif convert: plain = util.html_to_text(html)
        else: plain = u"Email rendered using HTML"

        html = html.encode(encoding)
        plain = plain.encode(encoding)

        mime = smtp.multipart()
        mime["Subject"] = subject
        mime["From"] = sender_mime
        mime["To"] = ", ".join(receivers_mime)

        for key, value in headers.iteritems(): mime[key] = value

        plain_part = smtp.plain(plain)
        html_part = smtp.html(html)
        mime.attach(plain_part)
        mime.attach(html_part)

        smtp.message(
            sender_base,
            receivers_base,
            mime,
            host = host,
            port = port,
            username = username,
            password = password,
            stls = stls
        )

    def template(
        self,
        template,
        content_type = "text/html",
        templates_path = None,
        detached = False,
        **kwargs
    ):
        # calculates the proper templates path defaulting to the current
        # instances template path in case no custom value was passed
        templates_path = templates_path or self.templates_path

        # sets the initial value for the result, this value should
        # always contain an utf-8 based string value containing the
        # results of the template engine execution
        result = None

        # "resolves" the provided template path, taking into account
        # things like localization, at the end of this method execution
        # the template path should be the best match according to the
        # current framework's rules and definitions
        template = self.template_resolve(
            template,
            templates_path = templates_path
        )

        # runs the template args method to export a series of symbols
        # of the current context to the template so that they may be
        # used inside the template as it they were the proper instance
        self.template_args(kwargs)

        # runs a series of template engine validation to detect the one
        # that should be used for the current context, returning the result
        # for each of them inside the result variable
        if self.jinja: result = self.template_jinja(
            template,
            templates_path = templates_path,
            **kwargs
        )

        # in case no result value is defined (no template engine ran) an
        # exception must be raised indicating this problem
        if result == None: raise exceptions.OperationalError(
            message = "No valid template engine found"
        )

        # in case there's no request currently defined or the template is
        # being rendered in a detached environment (eg: email rendering)
        # no extra operations are required and the result value is returned
        # immediately to the caller method (for processing)
        if not self.request or detached: return result

        # updates the content type vale of the request with the content type
        # defined as parameter for the template running and then returns the
        # resulting (string buffer) value to the caller method
        self.request.set_content_type(content_type)
        return result

    def template_jinja(self, template, templates_path = None, **kwargs):
        extension = self._extension(template)
        self.jinja.autoescape = extension in ESCAPE_EXTENSIONS
        self.jinja.loader.searchpath = [templates_path]
        template = self.jinja.get_template(template)
        return template.render(kwargs)

    def template_args(self, kwargs):
        for key, value in self.context.iteritems(): kwargs[key] = value
        kwargs["request"] = self.request
        kwargs["session"] = self.request.session
        kwargs["location"] = self.request.location

    def template_resolve(self, template, templates_path = None):
        """
        Resolves the provided template path, using the currently
        defined locale. It tries to find the best match for the
        template file falling back to the default (provided) template
        path in case the best one could not be found.

        An optional templates path value may be used to change
        the default path to be used in the resolution of the template.

        @type template: String
        @param template: Path to the template file that is going to
        be "resolved" trying to find the best locale match.
        @type templates_path: String
        @param templates_path: The path to the directory containing the
        template files to be used in the resolution.
        @rtype: String
        @return: The resolved version of the template file taking into
        account the existence or not of the best locale template.
        """

        # splits the provided template name into the base and the name values
        # and then splits the name into the base file name and the extension
        # part so that it's possible to re-construct the name with the proper
        # locale naming part included in the name
        base, name = os.path.split(template)
        fname, extension = name.split(".", 1)

        # creates the base file name for the target (locale based) template
        # and then joins the file name with the proper base path to create
        # the "full" target file name
        target = fname + "." + self.request.locale + "." + extension
        target = base + "/" + target if base else target

        # sets the fallback name as the "original" template path, because
        # that's the default and expected behavior for the template engine
        fallback = template

        # "joins" the target path and the templates (base) path to create
        # the fill path to the target template, then verifies if it exists
        # and in case it does sets it as the template name otherwise uses
        # the fallback value as the target template path
        target_f = os.path.join(templates_path, target)
        template = target if os.path.exists(target_f) else fallback
        return template

    def send_static(self, path, static_path = None, cache = False):
        return self.static(
            resource_path = path,
            static_path = static_path,
            cache = cache
        )

    def send_file(self, contents, content_type = None, etag = None):
        _etag = self.request.get_header("If-None-Match", None)
        not_modified = etag == _etag
        if not_modified: self.request.set_code(304); return ""
        if content_type: self.content_type(content_type)
        if etag: self.request.set_header("Etag", etag)
        return contents

    def content_type(self, content_type):
        self.request.content_type = str(content_type)

    def models_c(self):
        """
        Retrieves the complete set of valid model classes
        currently loaded in the application environment.

        A model class is considered to be a class that is
        inside the models module and that inherits from the
        base model class.

        @rtype: List
        @return: The complete set of model classes that are
        currently loaded in the application environment.
        """

        # creates the list that will hold the various model
        # class discovered through module analysis
        models_c = []

        # iterates over the complete set of items in the models
        # modules to find the ones that inherit from the base
        # model class for those are the real models
        for _name, value in self.models.__dict__.iteritems():
            # verifies if the current value in iteration inherits
            # from the top level model in case it does not continues
            # the loop as there's nothing to be done
            try: is_valid = issubclass(value, model.Model)
            except: is_valid = False
            if not is_valid: continue

            # adds the current value in iteration as a new class
            # to the list that hold the various model classes
            models_c.append(value)

        # returns the list containing the various model classes
        # to the caller method as expected by definition
        return models_c

    def resolve(self, identifier = "_id"):
        """
        Resolves the current set of model classes meaning that
        a list of tuples representing the class name and the
        identifier attribute name will be returned. This value
        may than be used to represent the model for instance in
        exporting/importing operations.

        @type identifier: String
        @param identifier: The name of the attribute that may be
        used to uniquely identify any of the model values.
        @rtype: List
        @return: A list containing a sequence of tuples with the
        name of the model (short name) and the name of the identifier
        attribute for each of these models.
        """

        # creates the list that will hold the definition of the current
        # model classes with a sequence of name and identifier values
        entities = []

        # retrieves the complete set of model classes registered
        # for the current application and for each of them retrieves
        # the name of it and creates a tuple with the name and the
        # identifier attribute name adding then the tuple to the
        # list of entities tuples (resolution list)
        models_c = self.models_c()
        for model_c in models_c:
            name = model_c._name()
            tuple = (name, identifier)
            entities.append(tuple)

        # returns the resolution list to the caller method as requested
        # by the call to this method
        return entities

    def field(self, name, default = None, cast = None):
        return self.get_field(name, default = default, cast = cast)

    def get_field(self, name, default = None, cast = None):
        value = default
        args = self.request.args
        if name in args: value = args[name][0]
        if cast and not value in (None, ""): value = cast(value)
        return value

    def get_request(self):
        return self.request

    def get_session(self):
        return self.request.session

    def get_logger(self):
        return self.logger

    def get_uptime(self):
        current_date = datetime.datetime.utcnow()
        delta = current_date - self.start_date
        return delta

    def get_uptime_s(self, count = 2):
        uptime = self.get_uptime()
        uptime_s = self._format_delta(uptime)
        return uptime_s

    def get_bundle(self, name):
        return self.bundles.get(name, None)

    def echo(self, value):
        return value

    def url_for(self, type, filename = None, *args, **kwargs):
        result = self._url_for(type, filename = filename, *args, **kwargs)
        if result == None: raise exceptions.AppierException(
            message = "Cannot resolve path for '%s'" % type
        )
        return result

    def asset_url(self, filename):
        return self.url_for("static", "assets/" + filename)

    def acl(self, token):
        return util.check_login(token, self.request)

    def to_locale(self, value):
        locale = self.request.locale
        bundle = self.get_bundle(locale)
        if not bundle: return value
        return bundle.get(value, value)

    def nl_to_br(self, value):
        return value.replace("\n", "<br/>\n")

    def escape_jinja(self, callable, eval_ctx, value):
        import jinja2
        if eval_ctx.autoescape: value = unicode(jinja2.escape(value))
        value = callable(value)
        if eval_ctx.autoescape: value = jinja2.Markup(value)
        return value

    def nl_to_br_jinja(self, eval_ctx, value):
        return self.escape_jinja(self.nl_to_br, eval_ctx, value)

    def script_tag(self, value):
        return "<script type=\"text/javascript\" src=\"%s\"></script>" % value

    def script_tag_jinja(self, eval_ctx, value):
        return self.escape_jinja(self.script_tag, eval_ctx, value)

    def css_tag(self, value):
        return "<link rel=\"stylesheet\" type=\"text/css\" href=\"%s\" />" % value

    def css_tag_jinja(self, eval_ctx, value):
        return self.escape_jinja(self.css_tag, eval_ctx, value)

    def date_time(self, value, format = "%d/%m/%Y"):
        """
        Formats the provided as a date string according to the
        provided date format.

        Assumes that the provided value represents a float string
        and that may be used as the based timestamp for conversion.

        @type value: String
        @param value: The base timestamp value string that is going
        to be used for the conversion of the date string.
        @type format: String
        @param format: The format string that is going to be used
        when formatting the date time value.
        @rtype: String
        @return: The resulting date time string that may be used
        to represent the provided value.
        """

        # tries to convert the provided string value into a float
        # in case it fails the proper string value is returned
        # immediately as a fallback procedure
        try: value_f = float(value)
        except: return value

        # creates the date time structure from the provided float
        # value and then formats the date time according to the
        # provided format and returns the resulting string
        date_time_s = datetime.datetime.utcfromtimestamp(value_f)
        return date_time_s.strftime(format).decode("utf-8")

    def static(
        self,
        data = {},
        resource_path = None,
        static_path = None,
        cache = True,
        prefix_l = 8
    ):
        # retrieves the proper static path to be used in the resolution
        # of the current static resource that is being requested
        static_path = static_path or self.static_path

        # retrieves the remaining part of the path excluding the static
        # prefix and uses it to build the complete path of the file and
        # then normalizes it as defined in the specification
        resource_path_o = resource_path or self.request.path[prefix_l:]
        resource_path_f = os.path.join(static_path, resource_path_o)
        resource_path_f = os.path.abspath(resource_path_f)
        resource_path_f = os.path.normpath(resource_path_f)

        # verifies if the provided path starts with the contents of the
        # static path in case it does not it's a security issue and a proper
        # exception must be raised indicating the issue
        is_sub = resource_path_f.startswith(static_path)
        if not is_sub: raise exceptions.SecurityError(
            message = "Invalid or malformed path",
            error_code = 401
        )

        # verifies if the resources exists and in case it does not raises
        # an exception about the problem (going to be serialized)
        if not os.path.exists(resource_path_f):
            raise exceptions.NotFoundError(
                message = "Resource '%s' does not exist" % resource_path_o,
                error_code = 404
            )

        # checks if the path refers a directory and in case it does raises
        # an exception because no directories are valid for static serving
        if os.path.isdir(resource_path_f):
            raise exceptions.NotFoundError(
                message = "Resource '%s' refers a directory" % resource_path_o,
                error_code = 404
            )

        # tries to use the current mime sub system to guess the mime type
        # for the file to be returned in the request and then uses this type
        # to update the request object content type value
        type, _encoding = mimetypes.guess_type(
            resource_path_o, strict = True
        )
        self.request.content_type = type

        # retrieves the last modified timestamp for the resource path and
        # uses it to create the etag for the resource to be served
        modified = os.path.getmtime(resource_path_f)
        etag = "appier-%.2f" % modified

        # retrieves the provided etag for verification and checks if the
        # etag remains the same if that's the case the file has not been
        # modified and the response should indicate exactly that
        _etag = self.request.get_header("If-None-Match", None)
        not_modified = etag == _etag

        # in case the file has not been modified a not modified response
        # must be returned inside the response to the client
        if not_modified: self.request.set_code(304); yield 0; return

        # retrieves the value of the range header value and updates the
        # is partial flag value with the proper boolean value in case the
        # header exists or not (as expected by specification)
        range_s = self.request.get_header("Range", None)
        is_partial = True if range_s else False

        # retrieves the size of the resource file in bytes, this value is
        # going to be used in the computation of the range values
        file_size = os.path.getsize(resource_path_f)

        # convert the current string based representation of the range
        # into a tuple based presentation otherwise creates the default
        # tuple containing the initial position and the final one
        if is_partial:
            range_s = range_s[6:]
            start_s, end_s = range_s.split("-", 1)
            start = int(start_s) if start_s else 0
            end = int(end_s) if end_s else file_size - 1
            range = (start, end)
        else: range = (0, file_size - 1)

        # creates the string that will represent the content range that is
        # going to be returned to the client in the current request
        content_range_s = "bytes %d-%d/%d" % (range[0], range[1], file_size)

        # retrieves the current date value and increments the cache overflow value
        # to it so that the proper expire value is set, then formats the date as
        # a string based value in order to be set in the headers
        current = datetime.datetime.utcnow()
        target = current + self.cache
        target_s = target.strftime("%a, %d %b %Y %H:%M:%S UTC")

        # sets the complete set of headers expected for the current request
        # this is done before the field yielding operation so that the may
        # be correctly sent as the first part of the message sending
        self.request.set_header("Etag", etag)
        if cache: self.request.set_header("Expires", target_s)
        else: self.request.set_header("Cache-Control", "no-cache, must-revalidate")
        if is_partial: self.request.set_header("Content-Range", content_range_s)
        if not is_partial: self.request.set_header("Accept-Ranges", "bytes")

        # in case the current request is a partial request the status code
        # must be set to the appropriate one (partial content)
        if is_partial: self.request.set_code(206)

        # calculates the real data size of the chunk that is going to be
        # sent to the client this must use the normal range approach then
        # yields this result because its going to be used by the upper layer
        # of the framework to "know" the correct content length to be sent
        data_size = range[1] - range[0] + 1
        yield data_size

        # opens the file for binary reading this is going to be used for the
        # complete reading of the contents, suing a generator based approach
        # this way static file serving may be fast and memory efficient
        file = open(resource_path_f, "rb")

        try:
            # seeks the file to the initial target position so that the reading
            # starts on the requested starting point as expected
            file.seek(range[0])

            # iterates continuously reading a series of chunks from the
            # the file until no value is returned (end of file) this chunks
            # are going to be yield to the parent method to be sent in a
            # recursive fashion (avoid memory problems)
            while True:
                if not data_size: break
                size = data_size if BUFFER_SIZE > data_size else BUFFER_SIZE
                data = file.read(size)
                if not data: break
                data_l = len(data)
                data_size -= data_l
                yield data
        finally:
            # in case there's an exception in the middle of the reading the
            # file must be correctly, in order to avoid extra leak problems
            file.close()

    def static_res(self, data = {}):
        static_path = os.path.join(self.res_path, "static")
        return self.static(
            data = data,
            static_path = static_path,
            prefix_l = 15
        )

    def icon(self, data = {}):
        pass

    def info(self, data = {}):
        return dict(
            name = self.name,
            service = self.service,
            type = self.type,
            server = self.server,
            host = self.host,
            port = self.port,
            ssl = self.ssl,
            status = self.status,
            uptime = self.get_uptime_s(),
            api_version = API_VERSION,
            date = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        )

    def version(self, data = {}):
        return dict(
            api_version = API_VERSION
        )

    @util.private
    def logging(self, data = {}, count = None, level = None):
        if not settings.DEBUG:
            raise exceptions.OperationalError(message = "Not in DEBUG mode")
        count = int(count) if count else 100
        level = level if level else None
        return dict(
            messages = self.handler_memory.get_latest(
                count = count,
                level = level
            )
        )

    @util.private
    def debug(self, data = {}):
        if not settings.DEBUG:
            raise exceptions.OperationalError(message = "Not in DEBUG mode")
        return dict(
            info = self.info(data),
            manager = self.manager.info()
        )

    def login(self, data = {}):
        params = self.request.get_params()
        secret = self.request.params.get("secret", (None,))[0]
        self.auth(**params)

        self.request.session.ensure()
        sid = self.request.session.sid

        self.on_login(sid, secret, **params)

        return dict(
            token = sid
        )

    def logout(self, data = {}):
        self.on_logout()

    def auth(self, username, password, **kwargs):
        is_valid = username == settings.USERNAME and password == settings.PASSWORD
        if not is_valid: raise exceptions.AppierException(
            message = "Invalid credentials provided",
            error_code = 403
        )

    def on_login(self, sid, secret, username = "undefined", **kwargs):
        self.request.session["username"] = username
        if secret: self.request.session["secret"] = secret

    def on_logout(self):
        if not self.request.session: return
        del self.request.session["username"]

    def _load_paths(self, offset = 1):
        element = inspect.stack()[offset]
        module = inspect.getmodule(element[0])
        self.file_path = os.path.dirname(__file__)
        self.appier_path = os.path.join(self.file_path, "..")
        self.appier_path = os.path.normpath(self.appier_path)
        self.base_path = os.path.dirname(module.__file__)
        self.base_path = os.path.normpath(self.base_path)
        self.root_path = os.path.join(self.base_path, "..")
        self.root_path = os.path.normpath(self.root_path)
        self.res_path = os.path.join(self.appier_path, "res")
        self.static_path = os.path.join(self.base_path, "static")
        self.controllers_path = os.path.join(self.base_path, "controllers")
        self.models_path = os.path.join(self.base_path, "models")
        self.templates_path = os.path.join(self.base_path, "templates")
        self.bundles_path = os.path.join(self.base_path, "bundles")
        if not self.base_path in sys.path: sys.path.append(self.base_path)
        if not self.root_path in sys.path: sys.path.append(self.root_path)

    def _load_config(self, apply = True):
        config.load(path = self.base_path)
        if apply: self._apply_config()

    def _load_settings(self):
        settings.DEBUG = config.conf("DEBUG", settings.DEBUG, cast = int)
        settings.USERNAME = config.conf("USERNAME", settings.USERNAME)
        settings.PASSWORD = config.conf("USERNAME", settings.PASSWORD)

    def _load_logging(self, level = None, format = log.LOGGING_FORMAT):
        level = level or logging.DEBUG
        level_s = config.conf("LEVEL", None)
        self.level = logging.getLevelName(level_s) if level_s else level
        self.formatter = logging.Formatter(format)
        self.logger = logging.getLogger(self.name)
        self.logger.parent = None
        self.logger.setLevel(self.level)

    def _load_handlers(self, handlers = None):
        # if the file logger handlers should be created, this value defaults
        # to false as file logging is an expensive operation
        file_log = bool(config.conf("FILE_LOG", False))

        # creates the various logging file names and then uses them to
        # try to construct the full file path version of them taking into
        # account the current operative system in use
        info_name = self.name + ".log"
        error_name = self.name + ".err"
        info_path = info_name if os.name == "nt" else "/var/log/" + info_name
        error_path = error_name if os.name == "nt" else "/var/log/" + error_name

        # "computes" the correct log levels that are going to be used in the
        # logging of certain handlers (most permissive option)
        info_level = self.level if self.level > logging.INFO else logging.INFO
        error_level = self.level if self.level > logging.ERROR else logging.ERROR

        # verifies if the current used has access ("write") permissions to the
        # currently defined file paths, otherwise default to the base name
        if file_log and not self._has_access(info_path, type = "a"): info_path = info_name
        if file_log and not self._has_access(error_path, type = "a"): error_path = error_name

        # creates both of the rotating file handlers that are going to be used
        # in the file logging of the current appier infra-structure note that
        # this logging handlers are only created in case the file log flag is
        # active so that no extra logging is used if not required
        try: self.handler_info = logging.handlers.RotatingFileHandler(
            info_path,
            maxBytes = MAX_LOG_SIZE,
            backupCount = MAX_LOG_COUNT
        ) if file_log else  None
        except: self.handler_info = None
        try: self.handler_error = logging.handlers.RotatingFileHandler(
            error_path,
            maxBytes = MAX_LOG_SIZE,
            backupCount = MAX_LOG_COUNT
        ) if file_log else None
        except: self.handler_error = None

        # creates the complete set of handlers that are  required or the
        # current configuration and the "joins" them under the handlers
        # list that my be used to retrieve the set of handlers
        self.handler_stream = logging.StreamHandler()
        self.handler_memory = log.MemoryHandler()
        self.handlers = handlers or (
            self.handler_info,
            self.handler_error,
            self.handler_stream,
            self.handler_memory
        )

        # updates the various handler configuration and then adds all
        # of them to the current logger with the appropriate formatter
        if self.handler_info:
            self.handler_info.setLevel(info_level)
            self.handler_info.setFormatter(self.formatter)
        if self.handler_error:
            self.handler_error.setLevel(error_level)
            self.handler_error.setFormatter(self.formatter)
        self.handler_stream.setLevel(self.level)
        self.handler_stream.setFormatter(self.formatter)
        self.handler_memory.setLevel(self.level)
        self.handler_memory.setFormatter(self.formatter)

        # iterates over the complete set of handlers currently registered
        # to add them to the current logger infra-structure so that they
        # are used when logging functions are called
        for handler in self.handlers:
            if not handler: continue
            self.logger.addHandler(handler)

    def _load_context(self):
        self.context["echo"] = self.echo
        self.context["url_for"] = self.url_for
        self.context["asset_url"] = self.asset_url
        self.context["acl"] = self.acl
        self.context["locale"] = self.to_locale
        self.context["nl_to_br"] = self.nl_to_br
        self.context["script_tag"] = self.script_tag
        self.context["css_tag"] = self.css_tag
        self.context["date_time"] = self.date_time
        self.context["field"] = self.field

    def _load_bundles(self):
        # creates the base dictionary that will handle all the loaded
        # bundle information and sets it in the current application
        # object reference so that may be used latter on
        bundles = dict()
        self.bundles = bundles

        # verifies if the current path to the bundle files exists in case
        # it does not returns immediately as there's no bundle to be loaded
        if not os.path.exists(self.bundles_path): return

        # list the bundles directory files and iterates over each of the
        # files to load its own contents into the bundles "registry"
        paths = os.listdir(self.bundles_path)
        for path in paths:
            # joins the current (base) bundles path with the current path
            # in iteration to create the full path to the file and opens
            # it trying to read its json based contents
            path_f = os.path.join(self.bundles_path, path)
            file = open(path_f, "rb")
            try: data_j = json.load(file)
            except: continue
            finally: file.close()

            # unpacks the current path in iteration into the base name,
            # locale string and file extension to be used in the registration
            # of the data in the bundles registry
            try: _base, locale, _extension = path.split(".", 2)
            except: continue

            # retrieves a possible existing map for the current locale in the
            # registry and updates such map with the loaded data, then re-updates
            # the reference to the locale in the current bundle registry
            bundle = bundles.get(locale, {})
            bundle.update(data_j)
            bundles[locale] = bundle

    def _load_controllers(self):
        # tries to import the controllers module and in case it
        # fails (no module is returned) returns the control flow
        # to the caller function immediately (nothing to be done)
        controllers = self._import("controllers")
        if not controllers: return

        # iterate over all the items in the controller module
        # trying to find the complete set of controller classes
        # to set them in the controllers map
        for key, value in controllers.__dict__.iteritems():
            # in case the current value in iteration is not a class
            # continues the iteration loop, nothing to be done for
            # non class value in iteration
            is_class = type(value) in (types.ClassType, types.TypeType)
            if not is_class: continue

            # verifies if the current value inherits from the base
            # controller class and in case it does not continues the
            # iteration cycle as there's nothing to be done
            is_controller = issubclass(value, controller.Controller)
            if not is_controller: continue

            # creates a new controller instance providing the current
            # app instance as the owner of it and then sets it the
            # resulting instance in the controllers map
            self.controllers[key] = value(self)

    def _load_models(self):
        self.models = self._import("models")
        if not self.models: return

        models_c = self.models_c()
        for model_c in models_c: model_c.setup()

    def _load_parts(self):
        parts = []

        for part in self.parts:
            is_class = inspect.isclass(part)
            if is_class: part = part(owner = self)
            else: part.register(self)
            name = part.name()
            routes = part.routes()
            self.part_routes.extend(routes)
            setattr(self, name + "_part", part)
            parts.append(part)

        self.parts = parts

    def _load_templating(self):
        self.load_jinja()

    def _load_patches(self):
        import email.charset
        email.charset.add_charset("utf-8", email.charset.SHORTEST)

    def _set_config(self):
        config.conf_s("APPIER_NAME", self.name)
        config.conf_s("APPIER_INSTANCE", self.instance)
        config.conf_s("APPIER_BASE_PATH", self.base_path)

    def _set_global(self):
        global APP
        APP = self

    def _apply_config(self):
        self.instance = config.conf("INSTANCE", None)
        self.name = config.conf("NAME", self.name)
        self.name = self.name + "-" + self.instance if self.instance else self.name

    def _set_locale(self):
        # normalizes the current locale string by converting the
        # last part of the locale string to an uppercase representation
        # and then re-joining the various components of it
        values = self.request.locale.split("_", 1)
        if len(values) > 1: values[1] = values[1].upper()
        locale_n = "_".join(values)
        locale_n = str(locale_n)

        # in case the current operative system is windows based an
        # extra locale conversion operation must be performed, after
        # than the proper setting of the os locale is done with the
        # fallback for exception being silent (non critical)
        if os.name == "nt": locale_n = defines.WINDOWS_LOCALE.get(locale_n, "")
        else: locale_n += ".utf8"
        try: locale.setlocale(locale.LC_ALL, locale_n)
        except: pass

    def _reset_locale(self):
        locale.setlocale(locale.LC_ALL, "")

    def _anotate_async(self):
        # verifies if the current response contains the location header
        # meaning that a redirection will occur, and if that's not the
        # case this function returns immediately to avoid problems
        if not "Location" in self.request.out_headers: return

        # checks if the current request is "marked" as asynchronous, for
        # such cases a special redirection process is applies to avoid the
        # typical problems with automated redirection using "ajax"
        is_async = True if self.field("async") else False
        if is_async: self.request.code = 280

    def _routes(self):
        if self.routes_v: return self.routes_v
        self._proutes()
        self.routes_v = self.routes()
        return self.routes_v

    def _proutes(self):
        """
        Processes the currently defined static routes taking
        the current instance as base for the function resolution.

        Note that some extra handler processing may occur for the
        resolution of the handlers for certain operations.

        Usage of this method may require some knowledge of the
        internal routing system as some of the operations are
        specific and detailed.
        """

        for route in App._BASE_ROUTES:
            function = route[2]
            context_s = route[3]

            method, name = self._resolve(function, context_s = context_s)
            self.names[name] = route
            route[2] = method

            del route[3]

        for handler in APP._ERROR_HANDLERS.itervalues():
            function = handler[0]
            context_s = handler[1]

            method, _name = self._resolve(function, context_s = context_s)
            handler[0] = method

    def _resolve(self, function, context_s = None):
        function_name = function.__name__

        if context_s == None: context = self
        else: context = self.controllers.get(context_s, None)

        if context_s == None: name = function_name
        else: name = util.base_name(context_s) + "." + function_name

        method = getattr(context, function_name)

        return method, name

    def _format_delta(self, time_delta, count = 2):
        days = time_delta.days
        hours, remainder = divmod(time_delta.seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        delta_s = ""
        if days > 0:
            delta_s += "%dd " % days
            count -= 1
        if count == 0: return delta_s.strip()
        if hours > 0:
            delta_s += "%dh " % hours
            count -= 1
        if count == 0: return delta_s.strip()
        if minutes > 0:
            delta_s += "%dm " % minutes
            count -= 1
        if count == 0: return delta_s.strip()
        delta_s += "%ds" % seconds
        return delta_s.strip()

    def _has_access(self, path, type = "w"):
        """
        Verifies if the provided path is accessible by the
        current used logged in to the system.

        Note that this method may left some garbage in case
        the file that is being verified does not exists.

        @type path: String
        @param path: The path to the file that is going to be verified
        for the provided permission types.
        @type type: String
        @param type: The type of permissions for which the file has
        going to be verifies (default to write permissions).
        @rtype: bool
        @return: If the file in the provided path is accessible
        by the currently logged in user.
        """

        has_access = True
        try: file = open(path, type)
        except: has_access = False
        finally: file.close()
        return has_access

    def _import(self, name):
        # tries to search for the requested module making sure that the
        # correct files exist in the current file system, in case they do
        # fails gracefully with no problems
        try: imp.find_module(name)
        except ImportError: return None

        # tries to import the requested module (relative to the currently)
        # executing path and in case there's an error raises the error to
        # the upper levels so that it is correctly processed, then returns
        # the module value to the caller method
        module = __import__(name)
        return module

    def _url_for(self, reference, filename = None, *args, **kwargs):
        """
        Tries to resolve the url for the provided type string (static or
        dynamic), filename and other dynamic arguments.

        This method is the inner protected method that returns invalid
        in case no resolution is possible and should not be used directly.

        Example values for type include (static, controller.method, etc.).

        @type reference: String
        @param reference: The reference string that is going to be used in
        the resolution of the urls (should conform with the standard).
        @type filename: String
        @param filename: The name (path) of the (static) file (relative to static
        base path) for the static file url to be retrieved.
        @rtype: String
        @return: The url that has been resolved with the provided arguments, in
        case no resolution was possible an invalid (unset) value is returned.
        """

        prefix = self.request.prefix
        if reference == "static":
            location = prefix + "static/" + filename
            return util.quote(location)
        elif reference == "appier":
            location = prefix + "appier/static/" + filename
            return util.quote(location)
        else:
            route = self.names.get(reference, None)
            if not route: return route

            route_l = len(route)
            opts = route[3] if route_l > 3 else {}

            base = opts.get("base", route[1].pattern)
            names_t = opts.get("names_t", {})

            base = base.rstrip("$")
            base = base.lstrip("^/")

            query = []

            for key, value in kwargs.iteritems():
                value_t = type(value)
                is_string = value_t in types.StringTypes
                if not is_string: value = str(value)
                replacer = names_t.get(key, None)
                if replacer:
                    base = base.replace(replacer, value)
                else:
                    key_q = util.quote(key)
                    value_q = util.quote(value)
                    param = key_q + "=" + value_q
                    query.append(param)

            location = prefix + base
            location = util.quote(location)

            query_s = "&".join(query)

            return location + "?" + query_s if query_s else location

    def _extension(self, file_path):
        _head, tail = os.path.split(file_path)
        tail_s = tail.split(".", 1)
        if len(tail_s) > 1: return "." + tail_s[1]
        return None

class APIApp(App):
    pass

class WebApp(App):

    def __init__(
        self,
        service = False,
        offset = 3,
        *args,
        **kwargs
    ):
        App.__init__(
            self,
            service = service,
            offset = offset,
            *args,
            **kwargs
        )
        decorator = util.error_handler(403)
        decorator(self.to_login)

    def handle_error(self, exception):
        # in case the current request is of type json (serializable) this
        # exception should not be handled using the template based strategy
        # but using the serialized based strategy instead
        if self.request.json: return App.handle_error(self, exception)

        # formats the various lines contained in the exception and then tries
        # to retrieve the most information possible about the exception so that
        # the returned map is the most verbose as possible (as expected)
        lines = traceback.format_exc().splitlines()
        message = hasattr(exception, "message") and\
            exception.message or str(exception)
        code = hasattr(exception, "error_code") and\
            exception.error_code or 500
        errors = hasattr(exception, "errors") and\
            exception.errors or None
        session = self.request.session
        sid = session and session.sid

        # in case the current running mode does not have the debugging features
        # enabled the lines value should be set as empty to avoid extra information
        # from being provided to the end user (as expected by specification)
        if not settings.DEBUG: lines = []

        # run the on error processor in the base application object and in case
        # a value is returned by a possible handler it is used as the response
        # for the current request (instead of the normal handler)
        result = self.call_error(exception, code = code)
        if result: return result

        # computes the various exception class related attributes, as part of these
        # attributes the complete (full) name of the exception should be included
        name = exception.__class__.__name__
        module = inspect.getmodule(exception)
        base_name = module.__name__ if module else None
        full_name = base_name + "." + name if base_name else name

        # calculates the path to the (base) resources related templates path, this is
        # going to be used instead of the (default) application related path
        templates_path = os.path.join(self.res_path, "templates")

        # renders the proper error template for the error with the complete set of
        # calculated attributes so that they may be displayed in the template
        return self.template(
            "error.html.tpl",
            templates_path = templates_path,
            exception = exception,
            name = name,
            full_name = full_name,
            lines = lines,
            message = message,
            code = code,
            errors = errors,
            session = session,
            sid = sid
        )

    def to_login(self, error):
        return self.redirect(
            self.url_for(
                "base.login",
                next = self.request.location,
                error = error.message
            )
        )

def get_app():
    return APP

def get_name():
    return APP.name

def get_request():
    return APP.get_request()

def get_session():
    return APP.get_session()