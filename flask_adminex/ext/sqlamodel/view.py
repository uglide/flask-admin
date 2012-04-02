from sqlalchemy.orm.attributes import InstrumentedAttribute
from sqlalchemy.orm.exc import NoResultFound
from sqlalchemy.orm import subqueryload
from sqlalchemy.sql.expression import desc
from sqlalchemy import or_

from wtforms import ValidationError, fields, validators
from wtforms.ext.sqlalchemy.orm import model_form, converts, ModelConverter
from wtforms.ext.sqlalchemy.fields import QuerySelectField, QuerySelectMultipleField

from flask import flash

from flask.ext.adminex import form
from flask.ext.adminex.model import BaseModelView
from flask.ext.adminex.ext.sqlamodel import filters, tools


class Unique(object):
    """Checks field value unicity against specified table field.

    :param get_session:
        A function that return a SQAlchemy Session.
    :param model:
        The model to check unicity against.
    :param column:
        The unique column.
    :param message:
        The error message.
    """
    field_flags = ('unique', )

    def __init__(self, db_session, model, column, message=None):
        self.db_session = db_session
        self.model = model
        self.column = column
        self.message = message

    def __call__(self, form, field):
        try:
            obj = (self.db_session.query(self.model)
                       .filter(self.column == field.data).one())

            if not hasattr(form, '_obj') or not form._obj == obj:
                if self.message is None:
                    self.message = field.gettext(u'Already exists.')
                raise ValidationError(self.message)
        except NoResultFound:
            pass


class AdminModelConverter(ModelConverter):
    """
        SQLAlchemy model to form converter
    """
    def __init__(self, view):
        super(AdminModelConverter, self).__init__()

        self.view = view

    def _get_label(self, name, field_args):
        if 'label' in field_args:
            return field_args['label']

        if self.view.rename_columns:
            return self.view.rename_columns.get(name)

        return None

    def convert(self, model, mapper, prop, field_args):
        kwargs = {
            'validators': [],
            'filters': []
        }

        if field_args:
            kwargs.update(field_args)

        if hasattr(prop, 'direction'):
            remote_model = prop.mapper.class_
            local_column = prop.local_remote_pairs[0][0]

            kwargs.update({
                'allow_blank': local_column.nullable,
                'label': self._get_label(prop.key, kwargs),
                'query_factory': lambda: self.view.session.query(remote_model)
            })

            if local_column.nullable:
                kwargs['validators'].append(validators.Optional())
            else:
                kwargs['validators'].append(validators.Required())

            if prop.direction.name == 'MANYTOONE':
                return QuerySelectField(widget=form.ChosenSelectWidget(),
                                        **kwargs)
            elif prop.direction.name == 'ONETOMANY':
                # Skip backrefs
                if not local_column.foreign_keys and self.view.hide_backrefs:
                    return None

                return QuerySelectMultipleField(
                                widget=form.ChosenSelectWidget(multiple=True),
                                **kwargs)
            elif prop.direction.name == 'MANYTOMANY':
                return QuerySelectMultipleField(
                                widget=form.ChosenSelectWidget(multiple=True),
                                **kwargs)
        else:
            # Ignore pk/fk
            if hasattr(prop, 'columns'):
                column = prop.columns[0]

                if column.foreign_keys or column.primary_key:
                    return None

                # If field is unique, validate it
                if column.unique:
                    kwargs['validators'].append(Unique(self.view.session,
                                                       model,
                                                       column))

                if not column.nullable:
                    kwargs['validators'].append(validators.Required())

            # Apply label
            kwargs['label'] = self._get_label(prop.key, kwargs)

            return super(AdminModelConverter, self).convert(model,
                                                            mapper,
                                                            prop,
                                                            kwargs)

    @converts('Date')
    def convert_date(self, field_args, **extra):
        field_args['widget'] = form.DatePickerWidget()
        return fields.DateField(**field_args)

    @converts('DateTime')
    def convert_datetime(self, field_args, **extra):
        field_args['widget'] = form.DateTimePickerWidget()
        return fields.DateTimeField(**field_args)

    @converts('Time')
    def convert_time(self, field_args, **extra):
        return form.TimeField(**field_args)


class ModelView(BaseModelView):
    """
        SQLALchemy model view

        Usage sample::

            admin = Admin()
            admin.add_view(ModelView(User, db.session))
    """

    hide_backrefs = True
    """
        Set this to False if you want to see multiselect for model backrefs.
    """

    auto_select_related = True
    """
        Enable automatic detection of displayed foreign keys in this view
        and perform automatic joined loading for related models to improve
        query performance.

        Please note that detection is not recursive: if `__unicode__` method
        of related model uses another model to generate string representation, it
        will still make separate database call.
    """

    list_select_related = None
    """
        List of parameters for SQLAlchemy `subqueryload`. Overrides `auto_select_related`
        property.

        For example::

            class PostAdmin(ModelAdmin):
                list_select_related = ('user', 'city')

        You can also use properties::

            class PostAdmin(ModelAdmin):
                list_select_related = (Post.user, Post.city)

        Please refer to the `subqueryload` on list of possible values.
    """

    searchable_columns = None
    """
        Collection of the searchable columns. Only text-based columns
        are searchable (`String`, `Unicode`, `Text`, `UnicodeText`).

        Example::

            class MyModelView(ModelView):
                searchable_columns = ('name', 'email')

        You can also pass columns::

            class MyModelView(ModelView):
                searchable_columns = (User.name, User.email)

        Following search rules apply:

        - If you enter *ZZZ* in the UI search field, it will generate *ILIKE '%ZZZ%'*
          statement against searchable columns.

        - If you enter multiple words, each word will be searched separately, but
          only rows that contain all words will be displayed. For example, searching
          for 'abc def' will find all rows that contain 'abc' and 'def' in one or
          more columns.

        - If you prefix your search term with ^, it will find all rows
          that start with ^. So, if you entered *^ZZZ*, *ILIKE 'ZZZ%'* will be used.

        - If you prefix your search term with =, it will do exact match.
          For example, if you entered *=ZZZ*, *ILIKE 'ZZZ'* statement will be used.
    """

    column_filters = None
    """
        Collection of the column filters.

        Can contain either field names or instances of :class:`flask.ext.adminex.ext.sqlamodel.filters.BaseFilter` classes.

        For example::

            class MyModelView(BaseModelView):
                column_filters = ('user', 'email')

        or::

            class MyModelView(BaseModelView):
                column_filters = (BooleanEqualFilter(User.name, 'Name'))
    """

    filter_converter = filters.FilterConverter()
    """
        Field to filter converter.

        Override this attribute to use non-default converter.
    """

    def __init__(self, model, session,
                 name=None, category=None, endpoint=None, url=None):
        """
            Constructor.

            `model`
                Model class
            `session`
                SQLALchemy session
            `name`
                View name. If not set, will default to model name
            `category`
                Category name
            `endpoint`
                Endpoint name. If not set, will default to model name
            `url`
                Base URL. If not set, will default to '/admin/' + endpoint
        """
        self.session = session

        self._search_fields = None
        self._search_joins_names = set()

        self._filter_joins_names = set()

        super(ModelView, self).__init__(model, name, category, endpoint, url)

        # Configuration
        if not self.list_select_related:
            self._auto_joins = self.scaffold_auto_joins()
        else:
            self._auto_joins = self.list_select_related

    # Internal API
    def _get_model_iterator(self):
        """
            Return property iterator for the model
        """
        return self.model._sa_class_manager.mapper.iterate_properties

    # Scaffolding
    def scaffold_pk(self):
        for p in self._get_model_iterator():
            if hasattr(p, 'columns'):
                for c in p.columns:
                    if c.primary_key:
                        return p.key

        return None

    def scaffold_list_columns(self):
        """
            Return list of columns from the model.
        """
        columns = []

        for p in self._get_model_iterator():
            # Filter by name
            if (self.excluded_list_columns and
                p.key in self.excluded_list_columns):
                continue

            # Verify type
            if hasattr(p, 'direction'):
                if p.direction.name == 'MANYTOONE':
                    columns.append(p.key)
            elif hasattr(p, 'columns'):
                # TODO: Check for multiple columns
                column = p.columns[0]

                if column.foreign_keys or column.primary_key:
                    continue

                columns.append(p.key)

        return columns

    def scaffold_sortable_columns(self):
        """
            Return dictionary of sortable columns.
            Key is column name, value is sort column/field.
        """
        columns = dict()

        for p in self._get_model_iterator():
            if hasattr(p, 'columns'):
                # Sanity check
                if len(p.columns) > 1:
                    raise Exception('Automatic form scaffolding is not supported' +
                                    ' for multi-column properties (%s.%s)' % (
                                                    self.model.__name__, p.key))

                column = p.columns[0]

                # Can't sort by on primary and foreign keys by default
                if column.foreign_keys or column.primary_key:
                    continue

                columns[p.key] = p.key

        return columns

    def _get_columns_for_field(self, field):
        if isinstance(field, basestring):
            attr = getattr(self.model, field, None)

            if field is None:
                raise Exception('Field %s was not found.' % field)
        else:
            attr = field

        if (not attr or
            not hasattr(attr, 'property') or
            not hasattr(attr.property, 'columns') or
            not attr.property.columns):
                raise Exception('Invalid field %s: does not contains any columns.' % field)

        return attr.property.columns

    def init_search(self):
        """
            Initialize search. Returns `True` if search is supported for this
            view.

            For SQLAlchemy, this will initialize internal fields: list of
            column objects used for filtering, etc.
        """
        if self.searchable_columns:
            self._search_fields = []
            self._search_joins_names = set()

            for p in self.searchable_columns:
                for column in self._get_columns_for_field(p):
                    column_type = type(column.type).__name__

                    if not self.is_text_column_type(column_type):
                        raise Exception('Can only search on text columns. ' +
                                        'Failed to setup search for "%s"' % p)

                    self._search_fields.append(column)

                    # If it belongs to different table - add a join
                    if column.table != self.model.__table__:
                        self._search_joins_names.add(column.table.name)

        return bool(self.searchable_columns)

    def is_text_column_type(self, name):
        """
            Verify if column type is text-based.

            Returns `True` for `String`, `Unicode`, `Text`, `UnicodeText`
        """
        return (name == 'String' or name == 'Unicode' or
                name == 'Text' or name == 'UnicodeText')

    def scaffold_filters(self, name):
        columns = self._get_columns_for_field(name)

        if len(columns) > 1:
            raise Exception('Can not filter more than on one column for %s' % name)

        column = columns[0]

        if not isinstance(name, basestring):
            visible_name = self.get_column_name(name.property.key)
        else:
            visible_name = self.get_column_name(name)

        type_name = type(column.type).__name__
        flt = self.filter_converter.convert(type_name,
                                            column,
                                            visible_name)

        if flt:
            # If there's relation to other table, do it
            if column.table != self.model.__table__:
                self._filter_joins_names.add(column.table.name)

        return flt

    def is_valid_filter(self, filter):
        """
            Verify that provided filter object is derived from the
            SQLAlchemy-compatible filter class.

            `filter`
                Filter object to verify.
        """
        return isinstance(filter, filters.BaseSQLAFilter)

    def scaffold_form(self):
        """
            Create form from the model.
        """
        return model_form(self.model,
                          form.BaseForm,
                          only=self.form_columns,
                          exclude=self.excluded_form_columns,
                          field_args=self.form_args,
                          converter=AdminModelConverter(self))

    def scaffold_auto_joins(self):
        """
            Return list of joined tables by going through the
            displayed columns.
        """
        relations = set()

        for p in self._get_model_iterator():
            if hasattr(p, 'direction'):
                if p.direction.name == 'MANYTOONE':
                    relations.add(p.key)

        joined = []

        for prop, name in self._list_columns:
            if prop in relations:
                joined.append(getattr(self.model, prop))

        return joined

    # Database-related API
    def get_list(self, page, sort_column, sort_desc, search, filters, execute=True):
        """
            Return models from the database.

            `page`
                Page number
            `sort_column`
                Sort column name
            `sort_desc`
                Descending or ascending sort
            `search`
                Search query
            `execute`
                Execute query immediately? Default is `True`
            `filters`
                List of filter tuples
        """

        # Will contain names of joined tables to avoid duplicate joins
        joins = set()

        query = self.session.query(self.model)

        # Apply search criteria
        if self._search_supported and search:
            # Apply search-related joins
            if self._search_joins_names:
                query = query.join(*self._search_joins_names)
                joins |= self._search_joins_names

            # Apply terms
            terms = search.split(' ')

            for term in terms:
                if not term:
                    continue

                stmt = tools.parse_like_term(term)
                filter_stmt = [c.ilike(stmt) for c in self._search_fields]
                query = query.filter(or_(*filter_stmt))

        # Apply filters
        if self._filters:
            # Apply search-related joins
            if self._filter_joins_names:
                new_joins = self._filter_joins_names - joins

                if new_joins:
                    query = query.join(*new_joins)
                    joins |= self._search_joins_names

            # Apply filters
            for flt, value in filters:
                query = self._filters[flt].apply(query, value)

        # Calculate number of rows
        count = query.count()

        # Auto join
        for j in self._auto_joins:
            query = query.options(subqueryload(j))

        # Sorting
        if sort_column is not None:
            if sort_column in self._sortable_columns:
                sort_field = self._sortable_columns[sort_column]

                # Try to handle it as a string
                if isinstance(sort_field, basestring):
                    # Create automatic join against a table if column name
                    # contains dot.
                    if '.' in sort_field:
                        parts = sort_field.split('.', 1)

                        if parts[0] not in joins:
                            query = query.join(parts[0])
                            joins.add(parts[0])
                elif isinstance(sort_field, InstrumentedAttribute):
                    table = sort_field.parententity.tables[0]

                    if table.name not in joins:
                        query = query.join(table)
                        joins.add(table.name)
                else:
                    sort_field = None

                if sort_field is not None:
                    if sort_desc:
                        query = query.order_by(desc(sort_field))
                    else:
                        query = query.order_by(sort_field)

        # Pagination
        if page is not None:
            query = query.offset(page * self.page_size)

        query = query.limit(self.page_size)

        # Execute if needed
        if execute:
            query = query.all()

        return count, query

    def get_one(self, id):
        """
            Return one model by its id.

            `id`
                Model
        """
        return self.session.query(self.model).get(id)

    # Model handlers
    def create_model(self, form):
        """
            Create model from form.

            `form`
                Form instance
        """
        try:
            model = self.model()
            form.populate_obj(model)
            self.session.add(model)
            self.session.commit()
            return True
        except Exception, ex:
            flash('Failed to create model. ' + str(ex), 'error')
            return False

    def update_model(self, form, model):
        """
            Update model from form.

            `form`
                Form instance
        """
        try:
            form.populate_obj(model)
            self.session.commit()
            return True
        except Exception, ex:
            flash('Failed to update model. ' + str(ex), 'error')
            return False

    def delete_model(self, model):
        """
            Delete model.

            `model`
                Model to delete
        """
        try:
            self.session.delete(model)
            self.session.commit()
            return True
        except Exception, ex:
            flash('Failed to delete model. ' + str(ex), 'error')
            return False