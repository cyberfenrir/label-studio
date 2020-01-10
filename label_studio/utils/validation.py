import logging
from .exceptions import ValidationError
try:
    import ujson as json
except:
    import json


class SkipField(Exception):
    pass


# django backend settings compatibility
class Settings:
    UPLOAD_DATA_UNDEFINED_NAME = '$undefined$'


_DATA_TYPES = {
    'Text': [str, int],
    'HyperText': [str],
    'Image': [str],
    'List': [list],
    'Dialog': [list]
}
logger = logging.getLogger(__name__)
settings = Settings()


class TaskValidator:
    """ Task Validator with project scheme configs validation. It is equal to TaskSerializer from django backend.
    """
    def __init__(self, project):
        self._project = project
        self.completion_count = 0
        self.prediction_count = 0

    @staticmethod
    def check_data(project, data):
        """ Validate data from task['data']
        """

        if data is None:
            raise ValidationError(f'Task is empty (None)')

        # assign undefined key name from data to the first key from config, e.g. for txt loading
        if settings.UPLOAD_DATA_UNDEFINED_NAME in data and project.data_types.keys():
            key = list(project.data_types.keys())[0]
            data[key] = data[settings.UPLOAD_DATA_UNDEFINED_NAME]
            del data[settings.UPLOAD_DATA_UNDEFINED_NAME]

        # iterate over data types from project
        for data_key, data_type in project.data_types.items():
            if data_key not in data:
                raise ValidationError(f'"{data_key}" key is expected in task data')

            expected_types = _DATA_TYPES.get(data_type, str)
            if not isinstance(data[data_key], tuple(expected_types)):
                raise ValidationError(f'data["{data_key}"]={data[data_key]} '
                                      f'is of type "{type(data[data_key])}", '
                                      f'but types "{expected_types}" are expected')

            if data_type == 'List':
                for item in data[data_key]:
                    key = 'text'  # FIXME: read key from config (elementValue from List)
                    if key not in item:
                        raise ValidationError(f'Each item from List must have key {key}')

        return data

    @staticmethod
    def check_data_and_root(project, data, dict_is_root=False):
        """ Check data consistent and data is dict with task or dict['task'] is task

        :param project:
        :param data:
        :param dict_is_root:
        :return:
        """
        try:
            TaskValidator.check_data(project, data)
        except ValidationError as e:
            if dict_is_root:
                raise ValidationError(e.detail[0] + ' [assume: item = {...}, item is task root] ')
            else:
                raise ValidationError(e.detail[0] + ' [assume: item = {"data": {...}}, item["data"] is task root]')

    def project(self):
        """ Take the project from context
        """
        return self._project

    @staticmethod
    def check_allowed(task):
        allowed = ['data', 'completions', 'predictions', 'meta']

        # check each task filled
        for key in task.keys():
            if key not in allowed:
                return False

        # task is required
        if 'data' not in task:
            return False

        # everything is ok
        return True

    @staticmethod
    def raise_if_wrong_class(task, key, class_def):
        if key in task and not isinstance(task[key], class_def):
            raise ValidationError(f'Task[{key}] must be {class_def}')

    def validate(self, task):
        """ Validate whole task with task['data'] and task['completions']. task['predictions']
        """
        # task is class
        if hasattr(task, 'data'):
            self.check_data_and_root(self.project(), task.data)
            return task

        # self.instance is loaded by get_object of view
        if hasattr(self, 'instance') and hasattr(self.instance, 'data'):
            if isinstance(self.instance.data, dict):
                data = self.instance.data
            elif isinstance(self.instance.data, str):
                try:
                    data = json.loads(self.instance.data)
                except ValueError as e:
                    raise ValidationError(f"Can't parse task data: {str(e)}")
            else:
                raise ValidationError(f'Field "data" must be string or dict, but "{type(self.instance.data)}" found')
            self.check_data_and_root(self.instance.project, data)
            return task

        # check task is dict
        if not isinstance(task, dict):
            raise ValidationError('Task root must be dict with "data", "meta", "completions", "predictions" fields')

        # task[data] | task[completions] | task[predictions] | task[meta]
        if self.check_allowed(task):
            # task[data]
            self.raise_if_wrong_class(task, 'data', (dict, list))
            self.check_data_and_root(self.project(), task['data'])

            # task[completions]: we can't use CompletionSerializer for validation
            # because it's much different with validation we need here
            self.raise_if_wrong_class(task, 'completions', list)
            for completion in task.get('completions', []):
                ok = 'result' in completion
                if not ok:
                    raise ValidationError('Completion must have "result" fields')

                # check result is list
                if not isinstance(completion.get('result', []), list):
                    raise ValidationError('"result" field in completion must be list')

            # task[predictions]
            self.raise_if_wrong_class(task, 'predictions', list)
            for prediction in task.get('predictions', []):
                ok = 'result' in prediction
                if not ok:
                    raise ValidationError('Prediction must have "result" fields')

                # check result is list
                if not isinstance(prediction.get('result', []), list):
                    raise ValidationError('"result" field in prediction must be list')

            # task[meta]
            self.raise_if_wrong_class(task, 'meta', (dict, list))

        # task is data as is, validate task as data and move it to task['data']
        else:
            self.check_data_and_root(self.project(), task, dict_is_root=True)
            task = {'data': task}

        return task

    @staticmethod
    def format_error(i, detail, item):
        if len(detail) == 1:
            code = f' {detail[0].code}' if detail[0].code != "invalid" else ''
            return f'Error{code} at item {i}: {detail[0]} :: {item}'
        else:
            errors = ', '.join(detail)
            codes = [d.code for d in detail]
            return f'Errors {codes} at item {i}: {errors} :: {item}'

    def to_internal_value(self, data):
        """ Body of run_validation for all data items
        """
        if data is None:
            raise ValidationError('All tasks are empty (None)')

        if not isinstance(data, list):
            raise ValidationError('data is not a list')

        if len(data) == 0:
            raise ValidationError('data is empty')

        ret, errors = [], []
        self.completion_count, self.prediction_count = 0, 0
        for i, item in enumerate(data):
            try:
                validated = self.validate(item)
            except ValidationError as exc:
                error = self.format_error(i, exc.detail, item)
                errors.append(error)
                # do not print to user too many errors
                if len(errors) >= 100:
                    errors[99] = '...'
                    break
            else:
                ret.append(validated)
                errors.append({})

                if 'completions' in item:
                    self.completion_count += len(item['completions'])
                if 'predictions' in item:
                    self.prediction_count += len(item['predictions'])

        if any(errors):
            logger.warning(f'Can\'t deserialize tasks due to {errors}')
            raise ValidationError(errors)

        return ret
