"""Service registry - pure business logic with no transport awareness."""

from dataclasses import dataclass


@dataclass
class ServiceEntry:
    name: str
    description: str
    input_model: type
    output_model: type
    func: object


_registry: list[ServiceEntry] = []


def service(*, name: str, description: str, input_model: type, output_model: type):
    """Decorator that registers a function as a service."""

    def decorator(func):
        _registry.append(
            ServiceEntry(
                name=name,
                description=description,
                input_model=input_model,
                output_model=output_model,
                func=func,
            )
        )
        return func

    return decorator


def get_registry() -> list[ServiceEntry]:
    """Return all registered services."""
    return list(_registry)
