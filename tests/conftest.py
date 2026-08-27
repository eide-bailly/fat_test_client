"""Shared pytest setup for fabric-scripts tests.

Makes the suite self-contained: puts `scripts/` on `sys.path` so the tests can import the
scripts directly (they are not a package), and stubs the heavy third-party packages
(`fabric_cicd`, `azure.identity`) that `deploy_fabric.py` / `_fabric_lro.py` import at
module-load time. This lets the suite run with only pytest, requests, pydantic, and pyyaml
installed.
"""

from __future__ import annotations

from pathlib import Path
import re
import sys
from types import ModuleType

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


def _install_module(name: str, **attrs: object) -> ModuleType:
    module = ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    return module


def _stub_requests() -> None:
    """Stub `requests` if it is not installed in this environment.

    `deploy_fabric.py` and `_fabric_lro.py` only need the `requests.Response` type (used in
    type hints, evaluated eagerly since neither module uses `from __future__ import
    annotations`) plus `requests.get`/`requests.post`, which every test that reaches an HTTP
    call monkeypatches with a fake anyway.
    """
    if "requests" in sys.modules:
        return

    class Response:
        pass

    def _unconfigured(*args: object, **kwargs: object) -> Response:
        raise NotImplementedError(
            "requests.get/requests.post is a test stub; monkeypatch it before use."
        )

    _install_module("requests", Response=Response, get=_unconfigured, post=_unconfigured)


def _stub_fabric_cicd() -> None:
    """Stub `fabric_cicd` (and the submodules `deploy_fabric` imports) if not installed."""
    if "fabric_cicd" in sys.modules:
        return

    class InvokeError(Exception):
        pass

    class FailedPublishedItemStatusError(Exception):
        def __init__(self, message: str, _logger: object = None) -> None:
            super().__init__(message)

    class PublishError(Exception):
        def __init__(self, errors: list, _logger: object = None) -> None:
            self.errors = errors
            failed_names = [name for name, _ in errors]
            super().__init__(f"Failed to publish {len(errors)} item(s): {failed_names}")

    class FabricWorkspace:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

    def publish_all_items(workspace: object) -> None:
        return None

    constants = _install_module(
        "fabric_cicd.constants",
        INVALID_FOLDER_CHAR_REGEX=r"[<>]",
        INDENT="",
    )
    check_utils = _install_module("fabric_cicd._common._check_utils", check_regex=re.compile)
    exceptions = _install_module(
        "fabric_cicd._common._exceptions",
        InvokeError=InvokeError,
        FailedPublishedItemStatusError=FailedPublishedItemStatusError,
        PublishError=PublishError,
    )
    logging_module = _install_module(
        "fabric_cicd._common._logging", log_header=lambda logger, message: None
    )
    common_pkg = _install_module(
        "fabric_cicd._common",
        _check_utils=check_utils,
        _exceptions=exceptions,
        _logging=logging_module,
    )
    _install_module(
        "fabric_cicd",
        constants=constants,
        _common=common_pkg,
        FabricWorkspace=FabricWorkspace,
        publish_all_items=publish_all_items,
    )


def _stub_azure_identity() -> None:
    """Stub `azure.identity.ClientSecretCredential` if `azure-identity` is not installed."""
    if "azure.identity" in sys.modules:
        return

    class ClientSecretCredential:
        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs

        def get_token(self, *scopes: str) -> object:
            # Never actually called: every test that reaches a real HTTP call monkeypatches
            # `_fabric_headers` (or the underlying `requests.get`/`requests.post`) instead.
            raise NotImplementedError("get_token is not implemented in the test stub.")

    identity_module = _install_module(
        "azure.identity", ClientSecretCredential=ClientSecretCredential
    )
    if "azure" in sys.modules:
        sys.modules["azure"].identity = identity_module
    else:
        _install_module("azure", identity=identity_module)


_stub_requests()
_stub_fabric_cicd()
_stub_azure_identity()
