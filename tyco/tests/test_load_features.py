import os
import json
import shutil
import tempfile
from pathlib import Path
import pytest

from tyco import load, loads

ROOT = Path(__file__).resolve().parents[1]


def test_loads_basic():
    """Test loads() function with basic tyco content."""
    content = """
str environment: production
int port: 8080

Server:
 *str name:
  int port:
  - web1, 80
  - api1, 3000
"""
    context = loads(content)
    data = context.to_json()
    
    assert data['environment'] == 'production'
    assert data['port'] == 8080
    assert len(data['Server']) == 2
    assert data['Server'][0]['name'] == 'web1'
    assert data['Server'][0]['port'] == 80


def test_loads_empty_content():
    """Test loads() with empty content."""
    context = loads("")
    data = context.to_json()
    assert data == {}


def test_loads_with_templates():
    """Test loads() with template expansion."""
    content = """
str env: staging
str region: us-west-2

Server:
 *str name:
  str env:
  str region:
  str hostname:
  - web1, staging, us-west-2, {name}-{env}-{region}
"""
    context = loads(content)
    data = context.to_json()
    
    assert data['Server'][0]['hostname'] == 'web1-staging-us-west-2'


def test_load_directory_structure(tmp_path):
    """Test loading an entire directory with multiple .tyco files."""
    # Create directory structure
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    
    # Create main config file
    main_config = config_dir / "main.tyco"
    main_config.write_text("""
str environment: production
str region: us-east-1

Database:
 *str name:
  str host:
  int port:
  - primary, db1.example.com, 5432
""")
    
    # Create servers config file
    servers_config = config_dir / "servers.tyco"
    servers_config.write_text("""
Server:
 *str name:
  Database db:
  str region:
  - web1, Database(primary), us-east-1
  - api1, Database(primary), us-west-2
""")
    
    # Create subdirectory with another config
    subdir = config_dir / "services"
    subdir.mkdir()
    service_config = subdir / "monitoring.tyco"
    service_config.write_text("""
Monitor:
 *str service:
  str endpoint:
  - prometheus, /metrics
  - grafana, /api/health
""")
    
    # Load the entire directory
    context = load(str(config_dir))
    data = context.to_json()
    
    # Verify all files were loaded
    assert data['environment'] == 'production'
    assert data['region'] == 'us-east-1'
    assert len(data['Database']) == 1
    assert len(data['Server']) == 2
    assert len(data['Monitor']) == 2
    
    # Verify references work across files
    assert data['Server'][0]['db']['name'] == 'primary'
    assert data['Server'][0]['db']['host'] == 'db1.example.com'


def test_load_directory_with_python_validators(tmp_path):
    """Test loading directory with Python validator modules."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    
    # Create tyco config file
    config_file = config_dir / "app.tyco"
    config_file.write_text("""
User:
 *str username:
  str email:
  int age:
  - alice, alice@example.com, 30
  - bob, bob@invalid-email, 25
  - charlie, charlie@example.com, -5
""")
    
    # Create corresponding Python validator
    validator_file = config_dir / "app.py"
    validator_file.write_text("""
import re
from tyco._parser import Struct

class User(Struct):
    def validate(self):
        # Validate email format
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, self.email):
            raise ValueError(f"Invalid email format: {self.email}")
        
        # Validate age is positive
        if self.age is not None and self.age < 0:
            raise ValueError(f"Age must be positive: {self.age}")
""")
    
    # Load the directory - validation should trigger once objects materialize
    with pytest.raises(ValueError, match="Invalid email format"):
        context = load(str(config_dir))
        context.to_object()


def test_load_directory_recursive_structure(tmp_path):
    """Test loading deeply nested directory structure."""
    # Create nested structure: config/env/prod/database.tyco
    base_dir = tmp_path / "config"
    env_dir = base_dir / "env" 
    prod_dir = env_dir / "prod"
    prod_dir.mkdir(parents=True)
    
    # Files at different levels
    (base_dir / "globals.tyco").write_text("str app_name: MyApp\n")
    (env_dir / "common.tyco").write_text("int timeout: 30\n")
    (prod_dir / "database.tyco").write_text("""
Database:
 *str name:
  str host:
  - main, prod-db.example.com
""")
    
    context = load(str(base_dir))
    data = context.to_json()
    
    assert data['app_name'] == 'MyApp'
    assert data['timeout'] == 30
    assert data['Database'][0]['name'] == 'main'


def test_load_single_file_vs_directory_compatibility(tmp_path):
    """Test that load() works the same for single files as before."""
    # Create a single file
    config_file = tmp_path / "single.tyco"
    config_file.write_text("""
str version: 1.0.0

Service:
 *str name:
  int replicas:
  - web, 3
  - api, 2
""")
    
    # Load as single file
    context = load(str(config_file))
    data = context.to_json()
    
    assert data['version'] == '1.0.0'
    assert len(data['Service']) == 2
    assert data['Service'][0]['replicas'] == 3


def test_python_validator_import_failure_graceful(tmp_path):
    """Test that invalid Python validators don't break loading."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    
    # Create tyco file
    (config_dir / "test.tyco").write_text("""
Item:
 *str name:
  - item1
""")
    
    # Create invalid Python file
    (config_dir / "test.py").write_text("""
# This has syntax errors
def invalid_syntax(
    missing_closing_paren
""")
    
    # Should still load successfully, just without validation
    context = load(str(config_dir))
    data = context.to_json()
    assert len(data['Item']) == 1


def test_load_directory_no_tyco_files(tmp_path):
    """Test loading directory with no .tyco files."""
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    
    # Create some non-tyco files
    (empty_dir / "readme.txt").write_text("Not a tyco file")
    (empty_dir / "config.json").write_text('{"key": "value"}')
    
    context = load(str(empty_dir))
    data = context.to_json()
    assert data == {}


def test_loads_vs_load_consistency(tmp_path):
    """Test that loads() and load() produce identical results for same content."""
    content = """
str app_name: MyApp

Config:
 *str env:
  str app:
  str full_name:
  - env: development, app: MyApp, full_name: {app}-{env}
  - env: production, app: MyApp, full_name: {app}-{env}
"""
    
    # Test with loads()
    context_loads = loads(content)
    data_loads = context_loads.to_json()
    
    # Test with load() using temp file
    temp_file = tmp_path / "test.tyco"
    temp_file.write_text(content)
    context_load = load(str(temp_file))
    data_load = context_load.to_json()
    
    assert data_loads == data_load


def test_directory_load_with_include_directives(tmp_path):
    """Test directory loading where files use #include directives."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    
    # Create base definitions
    (config_dir / "base.tyco").write_text("""
Database:
 *str name:
  str host:
  int port:
  - primary, db.example.com, 5432
""")
    
    # Create file that includes base
    (config_dir / "services.tyco").write_text("""
#include base.tyco

Service:
 *str name:
  Database db:
  - web-service, Database(primary)
""")
    
    context = load(str(config_dir))
    data = context.to_json()
    
    # Should work even though base.tyco gets loaded twice
    assert data['Service'][0]['db']['name'] == 'primary'
    assert data['Service'][0]['db']['port'] == 5432


def test_python_validator_with_custom_validation(tmp_path):
    """Test Python validator with custom validation logic."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    
    # Create config with port numbers
    (config_dir / "network.tyco").write_text("""
Port:
 *str name:
  int number:
  str protocol:
  - http, 80, tcp
  - https, 443, tcp
  - ssh, 22, tcp
  - invalid, 70000, tcp
""")
    
    # Create validator that checks port ranges
    (config_dir / "network.py").write_text("""
from tyco._parser import Struct

class Port(Struct):
    def validate(self):
        if not (1 <= self.number <= 65535):
            raise ValueError(f"Port number {self.number} out of valid range (1-65535)")
        if self.protocol not in ['tcp', 'udp']:
            raise ValueError(f"Invalid protocol: {self.protocol}")
""")
    
    # Should fail validation once materialized
    with pytest.raises(ValueError, match="Port number 70000 out of valid range"):
        context = load(str(config_dir))
        context.to_object()


def test_base_instance_parent_relationships():
    """Test that base instances (globals) have the globals dict as their parent."""
    content = """
str env: production
int max_connections: 1000

Person:
 *str name:
  int age:
  - "Alice", 30

# Global reference to person
Person admin: Person(Alice)
"""
    context = loads(content)
    
    globals_map = context._global_instance.inst_kwargs

    # Check that global attributes have globals container as parent (unchanged from your original design)
    env_attr = globals_map['env']
    assert env_attr.parent is globals_map
    
    max_connections_attr = globals_map['max_connections']
    assert max_connections_attr.parent is globals_map
    
    admin_attr = globals_map['admin']
    assert admin_attr.parent is globals_map


def test_template_expansion_with_global_access():
    """Test that instances can access global variables using global. syntax."""
    content = """
str environment: staging
str region: us-west-2

Server:
 *str name:
  str full_name:
  - "web", "{global.environment}-{name}-{global.region}"
  
# Global server instance that can access globals directly
str global_server_name: "{environment}-api-{region}"
"""
    context = loads(content)
    
    # Test that template expansion works for base instances accessing globals directly
    data = context.to_json()
    assert data['global_server_name'] == 'staging-api-us-west-2'
    
    # Test that server instances access globals with global. prefix
    assert data['Server'][0]['full_name'] == 'staging-web-us-west-2'


def test_colon_validation_in_unquoted_content():
    """Test that colons in unquoted content raise validation errors."""
    
    # Test case 1: URL with colon should fail if unquoted
    with pytest.raises(Exception, match="Colon : found in content - enclose in quotes"):
        content = """
str api_url: https://api.example.com/v1
"""
        loads(content)
    
    # Test case 2: Identifier-like content with colon should fail
    with pytest.raises(Exception, match="Colon : found in content - enclose in quotes"):
        content = """
str config: host:port
"""
        loads(content)
    
    # Test case 3: Times are OK because they start with digits (can't be field names)
    content = """
str start_time: 14:30:00
str end_time: 09:15:30
"""
    context = loads(content)
    data = context.to_json()
    assert data['start_time'] == '14:30:00'
    assert data['end_time'] == '09:15:30'


def test_colon_validation_with_quoted_content():
    """Test that properly quoted content with colons works correctly."""
    content = """
str api_url: 'https://api.example.com/v1'
str start_time: '14:30:00'
str config: 'host:port,timeout:30'
str description: "A service running at https://example.com:8080"
"""
    context = loads(content)
    data = context.to_json()
    
    assert data['api_url'] == 'https://api.example.com/v1'
    assert data['start_time'] == '14:30:00'
    assert data['config'] == 'host:port,timeout:30'
    assert data['description'] == 'A service running at https://example.com:8080'


def test_colon_validation_in_struct_instances():
    """Test colon validation within struct instance values."""
    
    # This test demonstrates how unquoted colons create unexpected field parsing
    # "service:config" becomes "service: config", creating a field named "service" instead of "endpoint"
    with pytest.raises(Exception, match="Invalid attribute endpoint"):
        content = """
Server:
 *str name:
  str endpoint:
  - "api", service:config
"""
        loads(content)
    
    # Should work: properly quoted values with colons
    content = """
Server:
 *str name:
  str url:
  str endpoint:
  - "api", "https://api.example.com", "service:config"
"""
    context = loads(content)
    data = context.to_json()
    assert data['Server'][0]['url'] == 'https://api.example.com'
    assert data['Server'][0]['endpoint'] == 'service:config'


def test_global_variable_access():
    """Test accessing global variables using global. syntax."""
    content = """
str company: "TechCorp"
str region: "us-west"

Person:
 *str name:
  str company:
  str email:
  - "Alice", "{global.company}", "{name}@{global.company}.com"

# Global instance can access globals directly
str server_name: "{company}-api-{region}"
"""
    context = loads(content)
    data = context.to_json()
    
    # Verify global access from struct instances
    assert data['Person'][0]['company'] == 'TechCorp'
    assert data['Person'][0]['email'] == 'Alice@TechCorp.com'
    
    # Verify direct global access
    assert data['server_name'] == 'TechCorp-api-us-west'


def test_explicit_syntax_requirements():
    """Test that the new explicit syntax requirements work as expected."""
    content = """
str company: "TechCorp"
str region: "us-west"

# Globals can access other globals directly
str message: "Company {company} operates in {region}"

# Struct instances must use explicit global. syntax
Person:
 *str name:
  str company_name:
  str email:
  - "Alice", "{global.company}", "{name}@{global.company}.com"
"""
    context = loads(content)
    data = context.to_json()
    
    # Verify direct global access works
    assert data['message'] == "Company TechCorp operates in us-west"
    
    # Verify explicit global. syntax works in struct instances
    assert data['Person'][0]['company_name'] == "TechCorp"
    assert data['Person'][0]['email'] == "Alice@TechCorp.com"


def test_global_field_name_precedence():
    """Test that a struct field named 'global' takes precedence over global scope access."""
    content = """
str company: "TechCorp"

Organization:
 *str name:
  str global:
  str description:
  - "LocalCorp", "local-value", "Using field: {global}"
"""
    context = loads(content)
    data = context.to_json()
    
    # Verify that {global} refers to the local field, not global scope
    assert data['Organization'][0]['global'] == "local-value"
    assert data['Organization'][0]['description'] == "Using field: local-value"


def test_to_object_basic_globals():
    """Ensure to_object() exposes simple global variables."""
    content = """
str environment: production
int port: 8080
bool debug: false
float version: 1.2

Server:
 *str name:
  - web1
"""
    context = loads(content)
    config = context.to_object()
    
    # Test dot notation access to global variables
    assert config.environment == "production"
    assert config.port == 8080
    assert config.debug is False
    assert config.version == 1.2


def test_to_object_with_complex_globals():
    """Ensure to_object() handles arrays and complex global variables."""
    content = """
str[] environments: ["dev", "staging", "prod"]
int[] ports: [8080, 8081, 8082]

Database:
 *str name:
  - primary
"""
    context = loads(content)
    config = context.to_object()
    
    # Test array access
    assert config.environments == ["dev", "staging", "prod"]
    assert config.ports == [8080, 8081, 8082]
    assert len(config.environments) == 3
    assert config.ports[0] == 8080


def test_to_object_empty_context():
    """Ensure to_object() still works without explicit globals."""
    content = """
Server:
 *str name:
  - web1
"""
    context = loads(content)
    config = context.to_object()
    
    # Should return an object with no attributes (but not fail)
    # We can check if accessing undefined attributes raises AttributeError
    with pytest.raises(AttributeError):
        _ = config.nonexistent_var


def test_to_object_globals_with_templates():
    """Ensure to_object() resolves templates declared in globals."""
    content = """
str env: staging
str region: us-west-2
str domain: example.com
str full_domain: "{env}.{region}.{domain}"

App:
 *str name:
  - myapp
"""
    context = loads(content)
    config = context.to_object()
    
    # Test that templated globals are expanded
    assert config.env == "staging"
    assert config.region == "us-west-2"
    assert config.domain == "example.com"
    assert config.full_domain == "staging.us-west-2.example.com"


def test_to_object_attribute_access_vs_dict():
    """Ensure to_object() returns an object that supports attribute access."""
    content = """
str app_name: MyApplication
int timeout: 30
"""
    context = loads(content)
    config = context.to_object()
    
    # Should work with dot notation
    assert config.app_name == "MyApplication"
    assert config.timeout == 30
    
    # Should not be a dictionary
    assert not isinstance(config, dict)
    
    # Should raise AttributeError for undefined attributes
    with pytest.raises(AttributeError):
        _ = config.undefined_attribute
