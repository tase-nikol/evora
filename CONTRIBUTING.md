# Contributing to Evora

Thank you for your interest in contributing to Evora! This document provides guidelines and instructions for contributing.

## 🎯 Project Vision

Evora aims to be a production-grade event processing runtime that **enforces reliability patterns at the framework level**. We prioritize:

1. **Reliability First**: Making it impossible to forget critical safeguards
2. **Developer Experience**: Clear APIs, helpful error messages, comprehensive documentation
3. **Production Readiness**: Battle-tested patterns, observability, and operational simplicity
4. **Progressive Complexity**: Simple for basic use cases, powerful for advanced scenarios

## 🚀 Getting Started

### Prerequisites

- Python 3.11 or higher
- Redis 6.0+ (for integration tests)
- Git

### Development Setup

1. **Clone the repository**
   ```bash
   git clone https://github.com/your-org/evora.git
   cd evora
   ```

2. **Create a virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -e ".[dev]"
   ```

4. **Run tests**
   ```bash
   pytest
   ```

5. **Start Redis for integration tests**
   ```bash
   docker run -p 6379:6379 redis:latest
   ```

## 📝 Development Guidelines

### Code Style

- We use **Ruff** for linting and formatting
- Line length: 100 characters
- Type hints are required for public APIs
- Docstrings for all public functions and classes

Run linting:
```bash
ruff check evora/
```

### Testing

- Write tests for all new features
- Maintain or improve code coverage
- Integration tests should clean up after themselves
- Use pytest fixtures for common setup

Run tests:
```bash
# All tests
pytest

# With coverage
pytest --cov=evora --cov-report=html

# Specific test file
pytest tests/test_schema_diff.py

# Specific test
pytest tests/test_schema_diff.py::test_field_addition
```

### Commit Messages

Follow conventional commits format:

```
<type>(<scope>): <subject>

<body>

<footer>
```

Types:
- `feat`: New feature
- `fix`: Bug fix
- `docs`: Documentation changes
- `test`: Test additions or changes
- `refactor`: Code refactoring
- `perf`: Performance improvements
- `chore`: Build/tool changes

Examples:
```
feat(broker): add Kafka broker implementation

Implements BaseBroker interface for Apache Kafka with
consumer group support and offset management.

Closes #123
```

```
fix(idempotency): handle TTL edge case

Fix race condition when TTL expires during check.
Add test coverage for the edge case.
```

## 🏗️ Architecture

### Key Components

- **`evora/core.py`**: Event contracts, envelopes, registry
- **`evora/app.py`**: Application layer, handler registry, subscribe decorator
- **`evora/runtime.py`**: Retry policies, execution logic
- **`evora/brokers/`**: Broker implementations (Redis, Memory)
- **`evora/idempotency.py`**: Idempotency interfaces
- **`evora/idempotency_redis.py`**: Redis-backed idempotency store
- **`evora/errors.py`**: Error classification
- **`evora/schema/`**: Schema governance tools
- **`evora/observability/`**: Telemetry and monitoring

### Design Principles

1. **Interface-Driven**: Define clear interfaces (BaseBroker, IdempotencyStore)
2. **Dependency Injection**: Pass dependencies explicitly, avoid global state
3. **Async First**: All I/O operations are async
4. **Type Safety**: Leverage Pydantic for validation
5. **Fail Fast**: Validate at startup, not at runtime

## 🔄 Pull Request Process

1. **Fork the repository** and create a feature branch
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. **Make your changes**
   - Write code
   - Add tests
   - Update documentation
   - Add changelog entry

3. **Run the test suite**
   ```bash
   pytest
   ruff check evora/
   ```

4. **Commit your changes**
   ```bash
   git add .
   git commit -m "[EVORA] feature(scope): description"
   ```

5. **Push to your fork**
   ```bash
   git push origin feature/your-feature-name
   ```

6. **Open a Pull Request**
   - Describe what changed and why
   - Reference any related issues
   - Include screenshots for UI changes
   - Ensure CI passes

### PR Review Process

- At least one maintainer approval required
- All tests must pass
- Code coverage should not decrease
- Documentation must be updated
- Breaking changes require discussion

## 🐛 Reporting Bugs

Use GitHub Issues with the bug template:

**Title**: Clear, concise description

**Description**:
- What you expected to happen
- What actually happened
- Steps to reproduce
- Environment (Python version, OS, Redis version)
- Relevant logs/stack traces

**Labels**: `bug`, `needs-triage`

## 💡 Requesting Features

Use GitHub Issues with the feature template:

**Title**: Clear feature description

**Description**:
- Problem you're trying to solve
- Proposed solution
- Alternative solutions considered
- Example code/API

**Labels**: `enhancement`, `needs-discussion`

## 📚 Documentation

Documentation is as important as code!

### Areas to Contribute

- **Tutorials**: Step-by-step guides for common use cases
- **Examples**: Working code examples in `examples/`
- **API Reference**: Docstrings and reference docs
- **Architecture**: Design decisions and patterns
- **Troubleshooting**: Common issues and solutions

### Documentation Style

- Use clear, simple language
- Include code examples
- Add diagrams where helpful
- Keep it up-to-date with code changes

## 📜 License

By contributing, you agree that your contributions will be licensed under the MIT License.

---

Thank you for contributing to Evora! 🎉
