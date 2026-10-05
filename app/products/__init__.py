"""Oracle EPM business-process modules.

The package intentionally performs no eager imports. Product contracts and
providers are used by worker startup paths, so keeping this initializer inert
prevents package-level circular imports as additional products are added.
"""
