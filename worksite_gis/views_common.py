from pathlib import Path, PureWindowsPath

from django.conf import settings
from django.http import JsonResponse

from .permissions import is_authenticated_user


def _error(message, status=400):
    return JsonResponse({"error": {"message": message}}, status=status)


def _catalog_path(value):
    raw = str(value).strip()
    windows_path = PureWindowsPath(raw)
    if windows_path.is_absolute():
        parts = [part for part in windows_path.parts if part not in {windows_path.drive, "\\", "/"}]
        data_index = next((index for index, part in enumerate(parts) if part.lower() == "data"), None)
        if data_index is not None:
            return settings.DATA_ROOT.joinpath(*parts[data_index + 1 :])
        return Path(windows_path.as_posix())
    path = Path(raw.replace("\\", "/"))
    if path.is_absolute():
        return path
    if path.parts and path.parts[0].lower() == "data":
        return settings.DATA_ROOT.joinpath(*path.parts[1:])
    return settings.BASE_DIR / path


def _layer_health_path(layer):
    metadata = layer.metadata if isinstance(layer.metadata, dict) else {}
    spatial = metadata.get("spatial", {}) if isinstance(metadata.get("spatial", {}), dict) else {}
    candidates = []
    if spatial.get("indexed"):
        candidates.append(spatial.get("index_path"))
    candidates.extend((metadata.get("output"), layer.path))
    paths = [_catalog_path(value) for value in candidates if isinstance(value, str) and value.strip()]
    return next((path for path in paths if path.exists()), paths[0] if paths else settings.DATA_ROOT)


def _layer_is_spatial_indexed(layer):
    metadata = layer.metadata if isinstance(layer.metadata, dict) else {}
    spatial = metadata.get("spatial", {}) if isinstance(metadata.get("spatial", {}), dict) else {}
    if spatial.get("indexed"):
        return True
    if layer.format != "spatial":
        return False
    path = Path(layer.path)
    if not path.is_absolute():
        path = settings.BASE_DIR / path
    return path.suffix.lower() in {".sqlite", ".sqlite3"} and path.exists()


def _staff_required(request):
    return request.user.is_authenticated and request.user.is_staff


def _authenticated_required(request):
    return request.user.is_authenticated


def _api_authentication_required(request):
    if not is_authenticated_user(request.user):
        return _error("authentication required", 401)
    return None


def _bounded_int(request, name, default, maximum):
    try:
        value = int(request.GET.get(name, str(default)))
    except (TypeError, ValueError):
        return None, _error(f"{name} must be numeric")
    return min(max(value, 1), maximum), None
