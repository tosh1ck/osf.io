# encoding: utf-8

import logging

from flask import request

from framework.celery_tasks import app
from framework.postcommit_tasks.handlers import run_postcommit

logger = logging.getLogger(__name__)

# TODO Find out why this is here and fix it.
collection = None
# if database._get_current_object() is not None:
#     collection = database['pagecounters']
# elif database._get_current_object() is None and settings.DEBUG_MODE:
#     logger.warn('Cannot connect to database. Analytics will be unavailable')
# else:
#     raise RuntimeError('Cannot connect to database')

@run_postcommit(once_per_request=False, celery=True)
@app.task(max_retries=5, default_retry_delay=60)
def increment_user_activity_counters(user_id, action, date_string):
    from osf.models import UserActivityCounter
    return UserActivityCounter.increment(user_id, action, date_string)


def get_total_activity_count(user_id):
    from osf.models import UserActivityCounter
    return UserActivityCounter.get_total_activity_count(user_id)


def build_page(rex, kwargs):
    """Build page key from format pattern and request data.

    :param str: Format string (e.g. `'{node}:{file}'`)
    :param dict kwargs: Data used to render format string
    """
    target_node = kwargs.get('node') or kwargs.get('project')
    target_id = target_node._id
    data = {
        'target_id': target_id,
    }
    data.update(kwargs)
    data.update(request.args.to_dict())
    try:
        return rex.format(**data)
    except KeyError:
        return None

def update_counter(page, node_info=None):
    """Update counters for page.

    :param str page: Colon-delimited page key in analytics collection
    """
    from osf.models import PageCounter
    return PageCounter.update_counter(page, node_info)


def get_basic_counters(page):
    from osf.models import PageCounter
    return PageCounter.get_basic_counters(page)
