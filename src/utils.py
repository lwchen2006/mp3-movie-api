import logging


def setup_logging(verbose=False):
    """Configure application logging (DEBUG when verbose, else INFO)."""
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )