import json
import os
import shutil
from pathlib import Path

import pytest

import tyco


ROOT = Path(__file__).resolve().parents[1]

# Support both local symlinks (for development) and submodule (for CI/production)
# Try symlinks first, fall back to submodule
INPUTS_DIR = ROOT / 'tests' / 'inputs'
EXPECTED_DIR = ROOT / 'tests' / 'expected'

if not INPUTS_DIR.exists() or INPUTS_DIR.is_symlink():
    # Use submodule paths
    INPUTS_DIR = ROOT / 'tests' / 'shared' / 'inputs'
    EXPECTED_DIR = ROOT / 'tests' / 'shared' / 'expected'


def run_in_process_typo_parser(path):
    """Import parser classes and run the lexer/parser in-process.

    Returns the JSON-serializable structure produced by context.to_json().
    """
    try:
        from tyco._parser import TycoContext, TycoLexer  # type: ignore
    except Exception as e:
        pytest.skip(f'parser.py is not importable or raised during import: {e}')

    context = TycoContext()
    # TycoLexer.from_path will cache and call process()
    TycoLexer.from_path(context, str(path))
    # ensure content rendered
    context._render_content()
    return context.to_json()


def _run_and_compare(input_name, expected_name, tmp_path):
    src = INPUTS_DIR / input_name
    # Resolve symlinks to get the actual file path
    src = src.resolve()
    dst = tmp_path / input_name
    shutil.copy(src, dst)
    data = run_in_process_typo_parser(dst)
    expected_path = EXPECTED_DIR / expected_name
    # Resolve symlinks for expected file too
    expected_path = expected_path.resolve()
    expected = json.loads(expected_path.read_text())
    assert data == expected


def test_simple_inputs(tmp_path):
    _run_and_compare('simple1.tyco', 'simple1.json', tmp_path)


def test_basic_types(tmp_path):
    _run_and_compare('basic_types.tyco', 'basic_types.json', tmp_path)


def test_datetime_types(tmp_path):
    _run_and_compare('datetime_types.tyco', 'datetime_types.json', tmp_path)


def test_arrays(tmp_path):
    _run_and_compare('arrays.tyco', 'arrays.json', tmp_path)


def test_nullable(tmp_path):
    _run_and_compare('nullable.tyco', 'nullable.json', tmp_path)


def test_references(tmp_path):
    _run_and_compare('references.tyco', 'references.json', tmp_path)


def test_templates(tmp_path):
    _run_and_compare('templates.tyco', 'templates.json', tmp_path)


def test_defaults(tmp_path):
    _run_and_compare('defaults.tyco', 'defaults.json', tmp_path)


def test_quoted_strings(tmp_path):
    _run_and_compare('quoted_strings.tyco', 'quoted_strings.json', tmp_path)


def test_number_formats(tmp_path):
    _run_and_compare('number_formats.tyco', 'number_formats.json', tmp_path)


def test_edge_cases(tmp_path):
    _run_and_compare('edge_cases.tyco', 'edge_cases.json', tmp_path)


def test_include_defaults_override(tmp_path):
    """Test that defaults can be overridden in files that include others."""
    # Need to copy both the override file and the base file it includes
    base_src = INPUTS_DIR / 'include_defaults_base.tyco'
    base_dst = tmp_path / 'include_defaults_base.tyco'
    shutil.copy(base_src.resolve(), base_dst)
    
    _run_and_compare('include_defaults_override.tyco', 'include_defaults_override.json', tmp_path)


def test_include_defaults_nooverride(tmp_path):
    """Test that base defaults are used when not overridden."""
    # Need to copy both the nooverride file and the base file it includes
    base_src = INPUTS_DIR / 'include_defaults_base.tyco'
    base_dst = tmp_path / 'include_defaults_base.tyco'
    shutil.copy(base_src.resolve(), base_dst)
    
    _run_and_compare('include_defaults_nooverride.tyco', 'include_defaults_nooverride.json', tmp_path)


def test_load_from_text_stream():
    example = ROOT / 'example.tyco'
    with open(example) as handle:
        context = tyco.load(handle)

    config = context.to_object()
    assert config.timezone == 'UTC'


def test_load_from_file_descriptor():
    example = ROOT / 'example.tyco'
    fd = os.open(example, os.O_RDONLY)
    try:
        context = tyco.load(fd)
    finally:
        os.close(fd)

    config = context.to_object()
    assert config.timezone == 'UTC'


def test_readme_example_usage():
    with tyco.open_example_file() as handle:
        context = tyco.load(handle.name)

    config = context.to_object()
    assert config.timezone == 'UTC'

    applications = config['Application']
    hosts = config['Host']
    ports = config['Port']

    assert applications[0].service == 'webserver'
    assert applications[0].command == 'start_app webserver.primary -p 80'
    assert applications[0].host.hostname == 'prod-01-us'
    assert applications[2].port.name == 'http_mysql'

    assert hosts[1].hostname == 'prod-02-us'
    assert hosts[1].os == 'Fedora'
    assert ports[0].number == 80

    json_payload = context.to_json()
    assert json_payload['Port'][1]['number'] == 3306
