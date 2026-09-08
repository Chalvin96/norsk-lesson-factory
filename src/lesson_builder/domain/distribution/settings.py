"""Not a check itself — distribution paths and staging settings used by export code."""

K_CATALOG_FILENAME = "catalog.json"
K_PACKET_PATH_PREFIX = "dist/lessons/"
K_AUDIO_PATH_PREFIX = "audio/lessons/"
K_REQUIRED_PACKAGE_FILES = ("plan.md", "lesson.md", "exercises.yaml")
K_DISTRIBUTION_STAGING_PREFIX = ".distribution-export-"


__all__ = [
    "K_AUDIO_PATH_PREFIX",
    "K_CATALOG_FILENAME",
    "K_PACKET_PATH_PREFIX",
    "K_REQUIRED_PACKAGE_FILES",
    "K_DISTRIBUTION_STAGING_PREFIX",
]
