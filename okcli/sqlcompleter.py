from __future__ import print_function, unicode_literals

import logging
from collections import Counter
from re import compile, escape

from okcli.lexer import ORACLE_KEYWORDS
from prompt_toolkit.completion import Completer, Completion

from .packages.completion_engine import suggest_type
from .packages.parseutils import last_word
from .packages.special.favoritequeries import favoritequeries

_logger = logging.getLogger(__name__)


class SQLCompleter(Completer):
    keywords = ORACLE_KEYWORDS

    str_functions = ['ASCII', 'ASCIISTR', 'CHR', 'COMPOSE', 'CONCAT', 'CONVERT',
                     'DECOMPOSE', 'DUMP', 'INITCAP', 'INSTR', 'INSTR2',
                     'INSTR4', 'INSTRB', 'INSTRC',
                     'LENGTH', 'LENGTH2', 'LENGTH4',
                     'LENGTHB', 'LENGTHC', 'LOWER', 'LPAD', 'LTRIM', 'NCHR',
                     'REGEXP_INSTR', 'REGEXP_REPLACE', 'REGEXP_SUBSTR',
                     'REPLACE', 'RPAD', 'RTRIM', 'SOUNDEX', 'SUBSTR',
                     'TRANSLATE', 'TRIM', 'UPPER', 'VSIZE', ]

    num_functions = ['ABS', 'ACOS', 'ASIN', 'ATAN', 'ATAN2',
                     'AVG', 'BITAND', 'CEIL', 'COS', 'COSH', 'COUNT', 'EXP',
                     'FLOOR', 'GREATEST', 'LEAST', 'LN', 'LOG', 'MAX', 'MEDIAN',
                     'MIN', 'MOD', 'POWER', 'REGEXP_COUNT', 'REMAINDER',
                     'ROUND', 'ROWNUM', 'SIGN', 'SIN', 'SINH', 'SQRT', 'SUM',
                     'TAN', 'TANH', 'TRUNC', ]

    date_functions = ['ADD_MONTHS', 'CURRENT_DATE', 'CURRENT_TIMESTAMP',
                      'DBTIMEZONE', 'EXTRACT', 'LAST_DAY', 'LOCALTIMESTAMP',
                      'MONTHS_BETWEEN', 'NEW_TIME', 'NEXT_DAY', 'ROUND',
                      'SESSIONTIMEZONE', 'SYSDATE', 'SYSTIMESTAMP', 'TRUNC',
                      'TZ_OFFSET', ]

    conv_functions = ['BIN_TO_NUM', 'CAST', 'CHARTOROWID', 'FROM_TZ',
                      'HEXTORAW', 'NUMTODSINTERVAL', 'NUMTOYMINTERVAL',
                      'RAWTOHEX', 'TO_CHAR', 'TO_CLOB', 'TO_DATE',
                      'TO_DSINTERVAL', 'TO_LOB', 'TO_MULTI_BYTE', 'TO_NCLOB',
                      'TO_NUMBER', 'TO_SINGLE_BYTE', 'TO_TIMESTAMP',
                      'TO_TIMESTAMP_TZ', 'TO_YMINTERVAL', ]

    analytic_functions = ['CORR', 'COVAR_POP', 'COVAR_SAMP', 'CUME_DIST',
                          'DENSE_RANK', 'FIRST_VALUE', 'LAG', 'LAST_VALUE',
                          'LEAD', 'LISTAGG', 'NTH_VALUE', 'RANK', 'STDDEV',
                          'VAR_POP', 'VAR_SAMP', 'VARIANCE', ]

    advanced_fuctions = ['BFILENAME', 'CARDINALITY', 'CASE', 'COALESCE',
                         'DECODE', 'EMPTY_BLOB', 'EMPTY_CLOB', 'GROUP_ID',
                         'LNNVL', 'NANVL', 'NULLIF', 'NVL', 'NVL2',
                         'SYS_CONTEXT',
                         'UID',
                         'USER',
                         'USERENV',
                         ]

    functions = sorted([x for x in
                        {x for x in
                         str_functions + num_functions + date_functions + conv_functions + analytic_functions + advanced_fuctions}
                        ])

    show_items = []

    # TODO
    change_items = []

    users = []

    def __init__(self, smart_completion=True, supported_formats=()):
        super(self.__class__, self).__init__()
        self.smart_completion = smart_completion
        self.reserved_words = set()
        for x in self.keywords:
            self.reserved_words.update(x.split())

        _logger.debug('reserved_words {}'.format(self.reserved_words))
        self.name_pattern = compile(r"^[_a-z][_a-z0-9\$]*$")

        self.special_commands = []
        self.table_formats = supported_formats
        self.reset_completions()

    def escape_name(self, name):
        return name

    def unescape_name(self, name):
        """Unquote a string."""
        if name and name[0] == '"' and name[-1] == '"':
            name = name[1:-1]

        return name

    def escaped_names(self, names):
        return [self.escape_name(name) for name in names]

    def extend_special_commands(self, special_commands):
        # Special commands are not part of all_completions since they can only
        # be at the beginning of a line.
        self.special_commands.extend(special_commands)

    def extend_database_names(self, databases):
        _logger.info('extending databases'.format(databases))
        self.databases.extend(databases)

    def extend_keywords(self, additional_keywords):
        self.keywords.extend(additional_keywords)
        self.all_completions.update(additional_keywords)

    def extend_show_items(self, show_items):
        for show_item in show_items:
            self.show_items.extend(show_item)
            self.all_completions.update(show_item)

    def extend_change_items(self, change_items):
        for change_item in change_items:
            self.change_items.extend(change_item)
            self.all_completions.update(change_item)

    def extend_users(self, users):
        _logger.debug('extending users {}'.format(users))
        for user in users:
            self.users.extend(user)
            self.all_completions.update(user)

    def extend_schemata(self, schemas):
        _logger.debug('extending schema {}'.format(schemas))
        for schema in schemas:
            self._extend_schemata(schema)

    def _extend_schemata(self, schema):
        # dbmetadata.values() are the 'tables' and 'functions' dicts
        _logger.debug('extending schema  with {}'.format(schema))
        schema = schema.upper()
        for metadata in self.dbmetadata.values():
            metadata[schema] = {}
        self.all_completions.update(schema)

    def extend_relations(self, data, kind, schema):
        """Extend metadata for tables or views

        :param data: list of (rel_name, ) tuples
        :param kind: either 'tables' or 'views'
        :return:
        """
        # 'data' is a generator object. It can throw an exception while being
        # consumed. This could happen if the user has launched the app without
        # specifying a database name. This exception must be handled to prevent
        # crashing.
        _logger.info('extending {} with {}'.format(kind, data))
        schema = schema.upper()

        try:
            data = [self.escaped_names(d) for d in data]
        except Exception:
            _logger.error('Error escaping data {}'.format(data), exc_info=True)
            data = []

        # dbmetadata['tables'][$schema_name][$table_name] should be a list of
        # column names. Default to an asterisk

        # TODO
        # add schema to data instead of self.dbname
        #
        metadata = self.dbmetadata[kind]
        for relname in data:
            name = relname[0]
            try:
                metadata[schema][name] = ['*']
            except KeyError:
                _logger.error('%r %r listed in unrecognized schema %r',
                              kind, name, schema)
            self.all_completions.add(name)

    def extend_columns(self, column_data, kind, schema):
        """Extend column metadata

        :param column_data: list of (rel_name, column_name) tuples
        :param kind: either 'tables' or 'views'
        :return:
        """
        schema = schema.upper()
        # 'column_data' is a generator object. It can throw an exception while
        # being consumed. This could happen if the user has launched the app
        # without specifying a database name. This exception must be handled to
        # prevent crashing.
        try:
            column_data = [self.escaped_names(d) for d in column_data]
        except Exception:
            column_data = []
        metadata = self.dbmetadata[kind]

        for relname, column in column_data:
            metadata[schema][relname].append(column)
            self.all_completions.add(column)

    def extend_functions(self, func_data, schema):
        # 'func_data' is a generator object. It can throw an exception while
        # being consumed. This could happen if the user has launched the app
        # without specifying a database name. This exception must be handled to
        # prevent crashing.
        try:
            func_data = [self.escaped_names(d) for d in func_data]
        except Exception:
            func_data = []

        # dbmetadata['functions'][$schema_name][$function_name] should return
        # function metadata.
        metadata = self.dbmetadata['functions']

        for func in func_data:
            metadata[schema][func[0]] = None
            self.all_completions.add(func[0])

    def set_dbname(self, dbname):
        self.dbname = dbname.upper()

    def reset_completions(self):
        self.databases = []
        self.users = []
        self.show_items = []
        self.dbname = ''
        self.dbmetadata = {'tables': {}, 'views': {}, 'functions': {}}
        self.all_completions = set(self.keywords + self.functions)

    @staticmethod
    def find_matches(text, collection, start_only=False, fuzzy=True):
        """Find completion matches for the given text.

        Given the user's input text and a collection of available
        completions, find completions matching the last word of the
        text.

        If `start_only` is True, the text will match an available
        completion only at the beginning. Otherwise, a completion is
        considered a match if the text appears anywhere within it.

        yields prompt_toolkit Completion instances for any matches found
        in the collection of available completions.
        """
        text = last_word(text, include='most_punctuations').lower()

        completions = []

        if fuzzy:
            regex = '.*?'.join(map(escape, text))
            pat = compile('(%s)' % regex)
            for item in sorted(collection):
                r = pat.search(item.lower())
                if r:
                    completions.append((len(r.group()), r.start(), item))
        else:
            match_end_limit = len(text) if start_only else None
            for item in sorted(collection):
                match_point = item.lower().find(text, 0, match_end_limit)
                if match_point >= 0:
                    completions.append((len(text), match_point, item))

        return (Completion(z, -len(text)) for x, y, z in sorted(completions))

    def get_completions(self, document, complete_event, smart_completion=None):
        word_before_cursor = document.get_word_before_cursor(WORD=True)
        if smart_completion is None:
            smart_completion = self.smart_completion

        # If smart_completion is off then match any word that starts with
        # 'word_before_cursor'.
        if not smart_completion:
            return self.find_matches(word_before_cursor, self.all_completions,
                                     start_only=True, fuzzy=False)

        completions = []
        suggestions = suggest_type(document.text, document.text_before_cursor)

        for suggestion in suggestions:

            _logger.debug('Suggestion type: %r', suggestion['type'])

            if suggestion['type'] == 'column':
                tables = suggestion['tables']
                _logger.debug("Completion column scope: %r", tables)
                scoped_cols = self.populate_scoped_cols(tables)
                if suggestion.get('drop_unique'):
                    # drop_unique is used for 'tb11 JOIN tbl2 USING (...'
                    # which should suggest only columns that appear in more than
                    # one table
                    scoped_cols = [
                        col for (col, count) in Counter(scoped_cols).items()
                        if count > 1 and col != '*'
                    ]

                cols = self.find_matches(word_before_cursor, scoped_cols)
                completions.extend(cols)

            elif suggestion['type'] == 'function':
                # suggest user-defined functions using substring matching
                funcs = self.populate_schema_objects(suggestion['schema'],
                                                     'functions')
                user_funcs = self.find_matches(word_before_cursor, funcs)
                completions.extend(user_funcs)

                # suggest hardcoded functions using startswith matching only if
                # there is no schema qualifier. If a schema qualifier is
                # present it probably denotes a table.
                # eg: SELECT * FROM users u WHERE u.
                if not suggestion['schema']:
                    predefined_funcs = self.find_matches(word_before_cursor,
                                                         self.functions,
                                                         start_only=True,
                                                         fuzzy=False)
                    completions.extend(predefined_funcs)

            elif suggestion['type'] == 'table':
                tables = self.populate_schema_objects(suggestion['schema'],
                                                      'tables')
                tables = self.find_matches(word_before_cursor, tables)
                completions.extend(tables)

            elif suggestion['type'] == 'view':
                views = self.populate_schema_objects(suggestion['schema'],
                                                     'views')
                views = self.find_matches(word_before_cursor, views)
                completions.extend(views)

            elif suggestion['type'] == 'alias':
                aliases = suggestion['aliases']
                aliases = self.find_matches(word_before_cursor, aliases)
                completions.extend(aliases)

            elif suggestion['type'] in ('database', 'schema'):
                dbs = self.find_matches(word_before_cursor, self.databases)
                completions.extend(dbs)

            elif suggestion['type'] == 'keyword':
                keywords = self.find_matches(word_before_cursor, self.keywords,
                                             start_only=True,
                                             fuzzy=False)
                completions.extend(keywords)

            elif suggestion['type'] == 'show':
                show_items = self.find_matches(word_before_cursor,
                                               self.show_items,
                                               start_only=False,
                                               fuzzy=True)
                completions.extend(show_items)

            elif suggestion['type'] == 'change':
                change_items = self.find_matches(word_before_cursor,
                                                 self.change_items,
                                                 start_only=False,
                                                 fuzzy=True)
                completions.extend(change_items)
            elif suggestion['type'] == 'user':
                users = self.find_matches(word_before_cursor, self.users,
                                          start_only=False,
                                          fuzzy=True)
                completions.extend(users)

            elif suggestion['type'] == 'special':
                special = self.find_matches(word_before_cursor,
                                            self.special_commands,
                                            start_only=True,
                                            fuzzy=False)
                completions.extend(special)
            elif suggestion['type'] == 'favoritequery':
                queries = self.find_matches(word_before_cursor,
                                            favoritequeries.list(),
                                            start_only=False, fuzzy=True)
                completions.extend(queries)
            elif suggestion['type'] == 'table_format':
                formats = self.find_matches(word_before_cursor,
                                            self.table_formats,
                                            start_only=True, fuzzy=False)
                completions.extend(formats)

        return completions

    def populate_scoped_cols(self, scoped_tbls):
        """Find all columns in a set of scoped_tables
        :param scoped_tbls: list of (schema, table, alias) tuples
        :return: list of column names
        """
        columns = []
        meta = self.dbmetadata

        for tbl in scoped_tbls:
            # A fully qualified schema.relname reference or default_schema
            # DO NOT escape schema names.
            schema = tbl[0] or self.dbname
            schema = schema.upper()
            relname = tbl[1]
            escaped_relname = self.escape_name(tbl[1])

            # We don't know if schema.relname is a table or view. Since
            # tables and views cannot share the same name, we can check one
            # at a time
            try:
                columns.extend(meta['tables'][schema][relname])

                # Table exists, so don't bother checking for a view
                continue
            except KeyError:
                try:
                    columns.extend(meta['tables'][schema][escaped_relname])
                    # Table exists, so don't bother checking for a view
                    continue
                except KeyError:
                    pass

            try:
                columns.extend(meta['views'][schema][relname])
            except KeyError:
                pass

        return columns

    def populate_schema_objects(self, schema, obj_type):
        """Returns list of tables or functions for a (optional) schema"""
        metadata = self.dbmetadata[obj_type]
        schema = schema or self.dbname
        schema = schema.upper()
        try:
            objects = metadata[schema].keys()
        except KeyError:
            # schema doesn't exist
            objects = []

        return objects

