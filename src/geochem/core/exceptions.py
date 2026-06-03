"""Custom exceptions for GeoChem."""


class GeoChemError(Exception):
    """Base exception for all GeoChem errors."""


class ProjectError(GeoChemError):
    """Error in project operations."""


class ProjectNotFoundError(ProjectError):
    """Project does not exist."""


class ProjectAlreadyExistsError(ProjectError):
    """Project already exists at the given path."""


class SchemaError(GeoChemError):
    """Error in schema operations."""


class SchemaValidationError(SchemaError):
    """Schema file is invalid or malformed."""


class SchemaFieldNotFoundError(SchemaError):
    """A required field is not found in the schema."""


class FileImportError(GeoChemError):
    """Error importing a file into the project."""


class DuplicateFileError(FileImportError):
    """File already exists in the project (same hash)."""


class ExtractionError(GeoChemError):
    """Error extracting data from a resource."""


class ExtractionFailedError(ExtractionError):
    """Extraction attempted but failed."""


class UnsafePathError(ExtractionError):
    """ZIP entry attempts path traversal."""


class LLMError(GeoChemError):
    """Error in LLM operations."""


class ProviderNotFoundError(LLMError):
    """Requested LLM provider is not registered."""


class ModelNotFoundError(LLMError):
    """Requested model is not available."""


class APIKeyError(LLMError):
    """API key is missing or invalid."""


class RateLimitError(LLMError):
    """Rate limit exceeded."""


class ProviderConnectionError(LLMError):
    """Cannot connect to the LLM provider."""


class MappingError(GeoChemError):
    """Error in field mapping operations."""


class ReviewError(GeoChemError):
    """Error in review operations."""


class ExportError(GeoChemError):
    """Error in data export."""


class DOIResolutionError(GeoChemError):
    """DOI not found or API error."""


class NetworkError(GeoChemError):
    """Network request failed."""


class WebExtractionError(GeoChemError):
    """Error extracting content from a web page."""


class BrowserAuthError(GeoChemError):
    """User did not confirm browser authentication."""
