"""Authored topology checks; these are not declarations of live availability."""
import json
from pathlib import Path
import tomllib

from hearth.scheduler.ontology import GPU_CATALOG_CONTRACT, load_model_catalog

ROOT = Path(__file__).resolve().parents[3]


def test_am4_authored_inventory_and_catalog_agree():
    nodes = tomllib.loads((ROOT/'fleet/inventory.toml').read_text(encoding='utf-8-sig'))['node']
    am4 = next(row for row in nodes if row['name'] == 'am4')
    doc = json.loads((ROOT/'knowledge/am4_gpu_catalog.json').read_text())
    assert len(am4['gpus']) == len(doc['cards']) == 2
    assert {row['uuid'] for row in am4['gpus']} == {row['uuid'] for row in doc['cards']}
    assert {row['bdf'] for row in am4['gpus']} == {row['bdf'] for row in doc['cards']}
    assert doc['resident_models'] == []  # Never promote a dated catalog into live state.


def test_native_catalog_retains_uncertainty_and_historical_rate():
    doc = json.loads((ROOT/'knowledge/am4_gpu_catalog.json').read_text())
    native = next(row for row in doc['models'] if row.get('alias') == 'am4-dense-27b')
    assert native['expected_gen_tps'] is None and not native['qualified_task_classes']
    assert len(native['card_charges_gb']) == 2
    historic = next(row for row in doc['models'] if row['model_id'] == 'qwen2.5:14b')
    assert historic['expected_gen_tps'] == 64.69 and '2026-09-15' in historic['quality_evidence']
    loaded = load_model_catalog(str(ROOT/'knowledge/am4_gpu_catalog.json'), contracts=(GPU_CATALOG_CONTRACT,))
    assert loaded['models']['am4-dense-27b'].card_charges_gb == {0:11.2,1:10.8}


def test_catalog_schema():
    import jsonschema
    schema = json.loads((ROOT/'hearth/contracts/gpu-catalog.v1.schema.json').read_text())
    doc = json.loads((ROOT/'knowledge/am4_gpu_catalog.json').read_text())
    jsonschema.validate(doc,schema)
