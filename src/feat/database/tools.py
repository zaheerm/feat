# F3AT - Flumotion Asynchronous Autonomous Agent Toolkit
# Copyright (C) 2010,2011 Flumotion Services, S.A.
# All rights reserved.

# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

# See "LICENSE.GPL" in the source distribution for more information.

# Headers in this file shall remain intact.
import optparse

from twisted.internet import reactor

from feat.database import view, driver, document
from feat.agencies.net import options, config
from feat.common import log, defer, error
from feat.agents.application import feat
from feat import applications

from feat.database.interface import ConflictError


def reset_documents(snapshot):
    applications.get_initial_data_registry().reset(snapshot)


def get_current_initials():
    return applications.get_initial_data_registry().get_snapshot()


def create_connection(host, port, name):
    db = driver.Database(host, port, name)
    return db.get_connection()


@defer.inlineCallbacks
def push_initial_data(connection, overwrite=False):
    documents = applications.get_initial_data_registry().itervalues()
    for doc in documents:
        try:
            yield connection.save_document(doc)
        except ConflictError:
            if not overwrite:
                log.error('script', 'Document with id %s already exists!',
                          doc.doc_id)
            else:

                yield _update_old(connection, doc)

    design_docs = view.generate_design_docs()
    for design_doc in design_docs:
        try:
            yield connection.save_document(design_doc)
        except ConflictError:
            yield _update_old(connection, design_doc)


@defer.inlineCallbacks
def _update_old(connection, doc):
    doc_id = doc.doc_id
    log.info('script', 'Updating old version of the document, id: %s', doc_id)
    rev = yield connection.get_revision(doc_id)
    doc.rev = rev
    yield connection.save_document(doc)


def load_application(option, opt_str, value, parser):
    splited = value.split('.')
    module, name = '.'.join(splited[:-1]), splited[-1]
    applications.load(module, name)


def parse_options(parser=None, args=None):
    if parser is None:
        parser = optparse.OptionParser()
        options.add_general_options(parser)
        options.add_db_options(parser)
        parser.add_option('-f', '--force', dest='force', default=False,
                          help=('Overwrite documents which are '
                                'already in the database.'),
                          action="store_true")
        parser.add_option('-m', '--migration', dest='migration', default=False,
                          help='Run migration script.',
                          action="store_true")
        parser.add_option('-a', '--application', nargs=1,
                          callback=load_application, type="string",
                          help='Load application by canonical name.',
                          action="callback")


    opts, args = parser.parse_args(args)
    opts.db_host = opts.db_host or options.DEFAULT_DB_HOST
    opts.db_port = opts.db_port or options.DEFAULT_DB_PORT
    opts.db_name = opts.db_name or options.DEFAULT_DB_NAME
    return opts, args


def create_db(connection):

    def display_warning(f):
        log.warning('script', 'Creating of database failed, reason: %s',
                    f.value)

    d = connection.create_database()
    d.addErrback(display_warning)
    return d


def script():
    log.init()
    log.FluLogKeeper.set_debug('4')

    opts, args = parse_options()
    c = config.DbConfig(host=opts.db_host, port=opts.db_port,
                        name=opts.db_name)
    with dbscript(c) as d:

        def body(connection):
            documents = applications.get_initial_data_registry().itervalues()
            log.info('script', "I will push %d documents.",
                     len(list(documents)))
            d = create_db(connection)
            d.addCallback(defer.drop_param, push_initial_data, connection,
                          opts.force)
            if opts.migration:
                d.addCallback(defer.drop_param, migration_script, connection)
            return d

        d.addCallback(body)


@defer.inlineCallbacks
def migration_script(connection):
    log.info("script", "Running the migration script.")
    for application in applications.get_application_registry().itervalues():
        keys = ApplicationVersions.key_for(application.name)
        version_doc = yield connection.query_view(ApplicationVersions, **keys)
        if not version_doc:
            to_run = application.get_migrations()
            version_doc = ApplicationVersion(name=unicode(application.name))
        else:
            version_doc = version_doc[0]
            to_run = [(version, migration)
                      for version, migration in application.get_migrations()
                      if version > version_doc.version]
        if not to_run:
            log.info("script", "There are no migrations for application %s "
                     "from version %s to %s", application.name,
                     version_doc.version, application.version)
            continue
        try:
            for version, migration in to_run:
                yield migration.run(connection._database)
                if isinstance(version, str):
                    version = unicode(version)
                version_doc.version = version
                yield connection.save_document(version_doc)
                log.info("script", "Successfully applied migration %r",
                         migration)

        except Exception as e:
            error.handle_exception("script", e, "Failed applying migration %r",
                                   migration)
            continue


class dbscript(object):

    def __init__(self, dbconfig):
        assert isinstance(dbconfig, config.DbConfig), str(type(dbconfig))
        self.config = dbconfig

    def __enter__(self):
        self.connection = create_connection(
            self.config.host, self.config.port, self.config.name)

        log.info('script', "Using host: %s, port: %s, db_name; %s",
                 self.config.host, self.config.port, self.config.name)

        self._deferred = defer.Deferred()
        return self._deferred

    def __exit__(self, type, value, traceback):
        self._deferred.addBoth(defer.drop_param, reactor.stop)
        reactor.callWhenRunning(self._deferred.callback, self.connection)
        reactor.run()


@feat.register_restorator
class ApplicationVersion(document.Document):

    type_name = 'application-version'

    document.field("name", None)
    document.field("version", None)


@feat.register_view
class ApplicationVersions(view.BaseView):

    name = 'application_versions'

    def map(doc):
        if doc['.type'] == 'application-version':
            yield doc.get('name'), None

    @staticmethod
    def key_for(name):
        return dict(key=name, include_docs=True)


@feat.register_view
class DocumentByType(view.BaseView):
    '''
    View used by migrations scripts, when querying with include_docs
    it returns unparsed object to make it easy to do custom manipulations
    before unserializing.
    '''

    name = 'by_type'

    def map(doc):
        yield doc['.type'], None

    @classmethod
    def parse_view_result(cls, rows, reduced, include_docs):
        if not include_docs:
            # return list of types
            return [x[0] for x in rows]
        else:
            # return unparsed docs
            return [x[3] for x in rows]


def standalone(script, options=[]):

    def define_options(extra_options):
        c = config.parse_service_config()

        parser = optparse.OptionParser()
        parser.add_option('--dbhost', '-H', action='store', dest='hostname',
                          type='str', help='hostname of the database',
                          default=c.db.host)
        parser.add_option('--dbname', '-n', action='store', dest='dbname',
                          type='str', help='name of database to use',
                          default=c.db.name)
        parser.add_option('--dbport', '-P', action='store', dest='dbport',
                          type='str', help='port of database to use',
                          default=c.db.port)

        for option in extra_options:
            parser.add_option(option)
        return parser

    def _error_handler(fail):
        error.handle_failure('script', fail, "Finished with exception: ")

    log.init()
    log.FluLogKeeper.set_debug('4')

    parser = define_options(options)
    opts, args = parser.parse_args()

    db = config.parse_service_config().db
    db.host, db.port, db.name = opts.hostname, opts.dbport, opts.dbname

    with dbscript(db) as d:
        d.addCallback(script, opts)
        d.addErrback(_error_handler)


@defer.inlineCallbacks
def view_aterator(connection, callback, view, view_keys=dict(),
                  args=tuple(), kwargs=dict(), per_page=15,
                  consume_errors=True):
    '''
    Asynchronous iterator for the view. Downloads a view in pages
    and calls the callback for each row.
    This helps avoid transfering data in huge datachunks.
    '''
    skip = 0
    while True:
        keys = dict(view_keys)
        keys.update(dict(skip=skip, limit=per_page))
        records = yield connection.query_view(view, **keys)
        log.debug('view_aterator', "Fetched %d records of the view: %s",
                  len(records), view.name)
        skip += len(records)
        for record in records:
            try:
                yield callback(connection, record, *args, **kwargs)
            except Exception as e:
                error.handle_exception(
                    'view_aterator', e,
                    "Callback %s failed its iteration on a row %r",
                    callback.__name__, record)
                if not consume_errors:
                    raise e

        if not records:
            break
