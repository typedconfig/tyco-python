# Tyco Python

[![PyPI version](https://badge.fury.io/py/tyco.svg)](https://badge.fury.io/py/tyco)
[![Python Version](https://img.shields.io/pypi/pyversions/tyco.svg)](https://pypi.org/project/tyco/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

A Python library for parsing and working with Tyco configuration files - a modern, type-safe configuration language designed for clarity and flexibility.

## 🚀 Quick Start

### Installation

```bash
pip install tyco
```

### Basic Usage

```python
import tyco

# Load the bundled example.tyco file (included in the package)
with tyco.open_example_file() as f:
  context = tyco.load(f.name)

# Materialize configuration as Python objects
config = context.to_object()

# Access global configuration values
environment = config.environment
debug = config.debug
timeout = config.timeout

# Access struct instances (lists of Structs)
databases = config.Database
servers = config['Server']      # Attribute or dict-style access both work

# Access individual instance fields
primary_db = databases[0]
db_host = primary_db.host
db_port = primary_db.port

# Export to JSON
json_data = context.to_json()
```

### Example Tyco File


The package includes a ready-to-use example Tyco file at:

  [tyco/example.tyco](https://github.com/typedconfig/tyco-python/blob/main/tyco/example.tyco)

You can inspect this file after installation, or load it directly as shown above.

```tyco
# Global configuration with type annotations
str environment: production
bool debug: false
int timeout: 30

# Database configuration struct
Database:
 *str name:           # Primary key field (*)
  str host:
  int port:
  str connection_string:
  # Instances
  - primary, localhost,    5432, "postgresql://localhost:5432/myapp"
  - replica, replica-host, 5432, "postgresql://replica-host:5432/myapp"

# Server configuration struct  
Server:
 *str name:           # Primary key for referencing
  int port:
  str host:
  ?str description:   # Nullable field (?) - can be null
  # Server instances
  - web1,    8080, web1.example.com,    description: "Primary web server"
  - api1,    3000, api1.example.com,    description: null
  - worker1, 9000, worker1.example.com, description: "Worker number 1"

# Feature flags array
str[] features: [auth, analytics, caching]
```

## ✨ Features

### 🎯 **Type Safety**
- **Strong Type Annotations**: `str`, `int`, `float`, `bool`, `date`, `time`, `datetime`
- **Array Types**: `int[]`, `str[]`, etc. for typed arrays
- **Nullable Types**: `?str`, `?int` for fields that can be `null`
- **Runtime Validation**: Type safety enforced during parsing

### 🏗️ **Structured Configuration**
- **Struct Definitions**: Define reusable configuration structures
- **Primary Key Fields**: `*` marks fields used for instance references
- **Nullable Fields**: `?` allows fields to have `null` values
- **Multiple Instances**: Create multiple instances of the same struct
- **Cross-References**: Reference instances by their primary key values

### 🔧 **Template System**
- **Variable Substitution**: Use `{variable}` syntax for dynamic values
- **Nested References**: `{struct.field}` for complex relationships
- **Global Access**: `{global.variable}` for explicit global scope
- **Template Expansion**: Automatic resolution during parsing

### 🌐 **Cross-Platform**
- **Pure Python**: No external dependencies
- **Python 3.8+**: Modern Python support
- **All Operating Systems**: Linux, macOS, Windows

## 📖 Language Features

### Type Annotations

```tyco
# Basic types
str app_name: MyApplication
int port: 8080
float timeout: 30.5
bool enabled: true

# Date and time types
date launch_date: 2025-01-01
time start_time: 09:00:00
datetime created_at: 2025-01-01 09:00:00Z

# Array types
str[] environments: [dev, staging, prod]
int[] ports: [80, 443, 8080]

# Nullable types (can be null)
?str description: null
?int backup_port: 8081
?str[] optional_tags: [tag1, tag2]
```

### Struct Definitions

```tyco
# Define a struct with primary key (*) and nullable (?) fields
User:
 *str username:        # Primary key field - used for references
  str email:           # Required field
 ?str full_name:       # Nullable field - can be null
 ?int age:             # Nullable with explicit value
  bool active: true    # Required field with default value
  # Create instances
  - admin, admin@example.com, "Administrator", 35, true
  - user1, user1@example.com, "John Doe", 28, true
  - guest, guest@example.com, null, null, false  # nulls for nullable fields

# Reference other struct instances using primary keys
Project:
 *str name:
  User owner:          # Reference to User struct by username
  str[] tags:
  - webapp, User(admin), [frontend, react]    # References user "admin"
  - api, User(user1), [backend, python, fastapi]
```

### Primary Keys and References

```tyco
# Structs with primary keys can be referenced
Host:
 *str name:           # Single primary key
  int cores:
  bool enabled:
  - web1, 4, true
  - db1, 8, false

Service:
 *str name:
 *str environment:    # Multiple primary keys
  Host host:
  int port:
  - auth, production, Host(web1), 8001
  - auth, staging, Host(db1), 8002

# Reference by primary key values
Deployment:
 *str name:
  Service service:
  - prod_auth, Service(auth, production)  # References by both primary keys
```

### Template Variables

```tyco
# Global variables for reuse
str environment: production
str region: us-east-1
str domain: example.com

# Template expansion in values
str api_url: https://api-{environment}-{region}.{domain}
str log_path: /var/log/{environment}

# Templates in struct instances with field access
Service:
 *str name:
  str url:
  str log_file:
  - auth, https://{name}-{environment}.{domain}, /logs/{environment}/{name}.log
  - users, https://{name}-{environment}.{domain}, /logs/{environment}/{name}.log

# Global scope access in templates
Config:
 *str key:
  str value:
  str message:
  - region_key, {region}, "Region is {global.region}"
  - env_key, {environment}, "Environment: {global.environment}"
```

### Nullable Values and Arrays

```tyco
# Nullable global values
?str optional_config: null
?str present_config: "I have a value"

# Nullable arrays
?int[] optional_numbers: null
?str[] tags: [tag1, tag2, tag3]

# Struct with nullable fields
Resource:
 *str id:
  str name:
 ?str description:     # Can be null
 ?str[] labels:        # Nullable array
 ?int priority:        # Nullable number
  # Instances with null values
  - res1, "Resource One", "A description", [prod, web], 10
  - res2, "Resource Two", null, null, null  # All nullable fields are null
  - res3, "Resource Three", null, [test], 5
```

## 🔧 API Reference

### Core Functions

#### `tyco.load(path: str | Path) -> TycoContext`
Parses one file (or every `*.tyco` file underneath a directory) and returns a rendered
`TycoContext`.

```python
import tyco

context = tyco.load("config.tyco")
```

#### `tyco.loads(content: str) -> TycoContext`
Parses Tyco configuration from an in-memory string—handy for tests.

```python
context = tyco.loads("""
str app_name: MyApp
int port: 8080
""")
```

Both helpers raise `tyco.TycoParseError` on syntax issues (subclass of `tyco.TycoException`).

### TycoContext Helpers

Once parsing succeeds you interact with the returned `TycoContext`.

```python
context = tyco.load("tyco/example.tyco")

config = context.to_object()
print(config.environment)     # -> "production"
print(config.timeout)         # -> 30

databases = config["Database"]
primary = databases[0]
print(primary.name, primary.host, primary.port)

json_payload = context.to_json()  # Plain dict ready for json.dumps(...)
```

- `to_object()` returns a `tyco.Struct` where both globals and struct definitions become
  attributes. Use attribute access (`config.debug`) or dictionary-style access (`config['debug']`).
- Struct names (e.g. `Database`) materialize as lists of typed `Struct` instances.
- `to_json()` materialises the canonical JSON-compatible dictionary (matching the shared test
  suite expectations).

### Working with References

References are resolved automatically—fields declared as another struct type give you the actual
instance:

```tyco
User:
 *str username:
  str email:
  - alice, alice@example.com
  - bob, bob@example.com

Project:
 *str name:
  User owner:
  - webapp, User(alice)
  - api,   User(bob)
```

```python
context = tyco.load("projects.tyco")
projects = context.to_object().Project

webapp = projects[0]
owner = webapp.owner           # Already resolved to the underlying User instance
print(f"{webapp.name} -> {owner.username} ({owner.email})")
```

### Custom Struct Classes

You can subclass `tyco.Struct` to add validation or helper methods. Registering a subclass lets the
parser materialise instances of your class automatically:

```python
import tyco

class Database(tyco.Struct):
    def validate(self):
        if self.port <= 0:
            raise ValueError("port must be positive")

context = tyco.load("tyco/example.tyco")
dbs = context.to_object().Database
print(isinstance(dbs[0], Database))  # True
```

### Bundled Example

Use `tyco.open_example_file()` to access the packaged `tyco/example.tyco` no matter where the
package is installed:

```python
with tyco.open_example_file() as handle:
    context = tyco.load(handle.name)
```

## 🧪 Testing

The library includes comprehensive tests covering all language features:

```bash
# Run tests
python -m pytest

# Run with coverage
python -m pytest --cov=tyco --cov-report=html
```

### Test Coverage
- ✅ **Type System**: All basic types, arrays, nullable types
- ✅ **Structs**: Primary keys, nullable fields, instances, defaults
- ✅ **References**: Primary key lookup and cross-references
- ✅ **Templates**: Variable substitution and nested references
- ✅ **Edge Cases**: Complex nesting, special characters, error handling

## 📁 Project Structure

```
tyco-python/
├── tyco/
│   ├── __init__.py          # Main API exports
│   ├── parser.py            # Core parsing logic and classes
│   └── tests/
│       ├── __init__.py
│       ├── test_parser.py   # Parser functionality tests
│       ├── test_load_features.py  # Load feature tests
│       ├── inputs/          # Test Tyco files
│       └── expected/        # Expected JSON outputs
├── pyproject.toml           # Project configuration
├── README.md               # This file
└── LICENSE                 # MIT License
```

## 🌟 Examples

### Web Application Configuration

```tyco
# app.tyco
str environment: production
bool debug: false
str secret_key: your-secret-key-here

# Database configuration with primary key
Database:
 *str name:               # Primary key for referencing
  str host:
  int port:
  str user:
 ?str password:           # Nullable - can be null for security
  str connection_string:
  - main, db.example.com, 5432, webapp_user, null, postgresql://{user}@{host}:{port}/{name}
  - cache, cache.example.com, 6379, cache_user, "secret123", redis://{host}:{port}

# Application servers  
Server:
 *str name:               # Primary key
  str host:
  int port:
  int workers:
 ?str description:        # Nullable description
  Database database:      # Reference to Database by name
  - web, 0.0.0.0, 8080, 4, "Main web server", Database(main)
  - api, 0.0.0.0, 8081, 2, null, Database(main)  # null description
  - worker, 127.0.0.1, 8082, 1, "Background worker", Database(cache)

# Feature flags (non-nullable array)
str[] enabled_features: [authentication, caching, analytics]

# Optional configuration (nullable)
?str[] optional_modules: [reporting, monitoring]
?int max_connections: null
```

```python
# app.py
import tyco

config = tyco.load('app.tyco')

# Use configuration
print(f"Environment: {config.environment}")
print(f"Debug mode: {config.debug}")

# Server configuration with references
for server in config.Server:
    db = server.database  # This is the actual Database instance
    desc = server.description or "No description"
    print(f"Server {server.name}: {server.host}:{server.port}")
    print(f"  Description: {desc}")
    print(f"  Database: {db.name} at {db.host}:{db.port}")
    
    # Handle nullable database password
    if db.password is not None:
        print(f"  Database has password configured")
    else:
        print(f"  Database password is null (using other auth)")

# Handle nullable configuration
if config.optional_modules is not None:
    print(f"Optional modules: {', '.join(config.optional_modules)}")
else:
    print("No optional modules configured")
```

### Microservices with Multi-Key References

```tyco
# services.tyco
str environment: staging
str base_domain: internal.company.com
int default_timeout: 30

# Services with compound primary key
Service:
 *str name:
 *str region:           # Multiple primary keys
  str host:
  int port:
  int timeout:
 ?str health_endpoint:  # Nullable health check
  - auth, us-east, auth-east.{base_domain}, 8001, {default_timeout}, /health
  - auth, us-west, auth-west.{base_domain}, 8001, {default_timeout}, /health
  - users, us-east, users-east.{base_domain}, 8002, {default_timeout}, null
  - payments, us-east, payments-east.{base_domain}, 8003, 60, /status

# Load balancer referencing services by compound keys
LoadBalancer:
 *str name:
  Service[] upstream_services:  # Array of service references
  str algorithm:
 ?int max_connections:          # Nullable configuration
  - east_lb, [
      Service(auth, us-east),
      Service(users, us-east),
      Service(payments, us-east)
    ], round_robin, 1000
  - west_lb, [Service(auth, us-west)], round_robin, null

# Monitoring with nullable fields
Monitor:
 *str service_name:
  Service service:
 ?str custom_endpoint:          # Override default health endpoint
  int check_interval:
  - auth_east_monitor, Service(auth, us-east), null, 30
  - users_east_monitor, Service(users, us-east), /api/status, 30
```

## 🤝 Contributing

We welcome contributions! The parser implementation follows these principles:

1. **Type Safety**: Strong type checking and validation
2. **Clarity**: Clean, readable configuration syntax  
3. **Flexibility**: Support for complex referencing and nullable fields
4. **Performance**: Efficient parsing for large configuration files
5. **Reliability**: Comprehensive test coverage for all features

### Development Setup

```bash
# Clone the repository
git clone https://github.com/typedconfig/tyco-python.git
cd tyco-python

# Install development dependencies
pip install -e .
pip install pytest pytest-cov

# Run tests
python -m pytest
```

## �� Requirements

- **Python**: 3.8 or higher
- **Dependencies**: None (pure Python implementation)
- **Operating Systems**: Linux, macOS, Windows

## �� Related Projects

- **[Tyco C++](https://github.com/typedconfig/tyco-cpp)**: C++ implementation with hash map architecture
- **[Tyco Web](https://github.com/typedconfig/web)**: Interactive playground and language documentation
- **[Language Specification](https://typedconfig.io)**: Complete Tyco language reference

## 📄 License

MIT License - see the [LICENSE](LICENSE) file for details.

## 🌍 Learn More

- **Website**: [https://typedconfig.io](https://typedconfig.io)
- **Documentation**: [Language specification and examples](https://typedconfig.io)
- **Repository**: [https://github.com/typedconfig/tyco-python](https://github.com/typedconfig/tyco-python)
