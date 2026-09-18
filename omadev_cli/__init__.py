"""omadev: start and stop whole development projects, idempotently.

The package is standard library only. `cli` is the entry point, `config`
owns the projects file. Launch steps arrive in later modules; each one is
check-then-act and reports what it did rather than assuming.
"""

__version__ = "0.1.0"
