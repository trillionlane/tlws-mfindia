"""Wide dataset projections must retain every field within PostgreSQL limits."""
import math
import re

import pytest

from mfdataindia.datasets import reads


@pytest.mark.parametrize('width',[49,50,51,84,100,101,256])
def test_json_object_calls_stay_within_postgres_arguments_without_dropping_fields(width):
    entry={'columns':[{'name':'field_'+str(i),'type':'text','nullable':True} for i in range(width)]}
    query=reads.projection(entry).as_string()
    calls=query.split('jsonb_build_object(')[1:]
    assert len(calls)==math.ceil(width/50)
    assert [int(n) for n in re.findall(r"'field_([0-9]+)'",query)]==list(range(width))
    assert all(len(re.findall(r"'field_[0-9]+'",call))<=50 for call in calls)
    assert all('to_jsonb("field_'+str(i)+'")' in query for i in range(width))


def test_empty_internal_projection_retains_prior_json_object_semantics():
    assert reads.projection({'columns':[]}).as_string()=='jsonb_build_object()'
