import cProfile
import io
import json
import logging
import pstats
import sys
import zipfile
from datetime import datetime
from pathlib import Path

import fire
import requests
from sqlmodel import Session, SQLModel, create_engine, select
from typeguard import typechecked

from repovul.config import Config
from repovul.converter import Converter
from repovul.models.repovul import RepovulItem, RepovulRevision

logging.basicConfig(
    level=logging.INFO, format="%(levelname)s-%(process)d-%(asctime)s - %(message)s"
)


def download():
    """Download and extract the data from the osv.dev dataset."""
    ecosystems = Config.ecosystems
    url_template = "https://osv-vulnerabilities.storage.googleapis.com/{ecosystem}/all.zip"
    for ecosystem in ecosystems:
        destination = Config.paths.osv / ecosystem
        url = url_template.format(ecosystem=ecosystem)
        logging.info(f"Downloading data from {ecosystem}...")
        response = requests.get(url, timeout=30)
        z = zipfile.ZipFile(io.BytesIO(response.content))
        z.extractall(destination)


def convert_one(repo_url: str) -> None:
    return Converter().convert_one(repo_url)


def convert_all() -> None:
    return Converter().convert_all()


def convert_range(start: int, end: int) -> None:
    return Converter().convert_range(start, end)


@typechecked
def get_by_commit(commit_hash: str, after: str | None = None, before: str | None = None):
    """
    Get the Repovul items that match a commit hash.

    The commit hash must be 40 characters long.

    The list can be filtered with the optional `after` and `before` parameters, which must be ISO
    8601 dates.

    `after` is included, and `before` is excluded, i.e. the possible options are: (1) after <= date,
    (2) after <= date < before, (3) date < before.
    """
    if len(commit_hash) != 40:
        raise ValueError("The commit hash must be 40 characters long.")
    engine = create_engine(f"sqlite:///{Config.paths.db}/repovul.db")
    with Session(engine) as session:
        # FIXME this isn't very clean (tests if the commit hash is part of the json string of the commit array)
        # This SQL would be better, but I can't find a way to convert it to sqlalchemy:
        # SELECT r.* FROM repovulitem r, JSON_EACH(r.commits) as jc WHERE jc.value = 'commit_hash';

        # type ignore used because RepovulItem.commits doesn't have
        # a `contains` method, but this is valid sqlalchemy.
        query = select(RepovulItem).where(RepovulItem.commits.contains(commit_hash))  # type: ignore[attr-defined]

        if after:
            start_date = datetime.fromisoformat(after)
            query = query.where(RepovulItem.published >= start_date)
        if before:
            end_date = datetime.fromisoformat(before)
            query = query.where(RepovulItem.published < end_date)

        results = session.exec(query).all()
        results_json = [item.to_dict() for item in results]
        print(json.dumps(results_json, indent=2))


def get_projects():
    """Get the list of repo URLs."""
    engine = create_engine(f"sqlite:///{Config.paths.db}/repovul.db")
    with Session(engine) as session:
        query = select(RepovulItem.repo_url).distinct()
        results = session.exec(query).all()
        print(json.dumps(results, indent=2))


def get_by_project(repo_url: str, after: str | None = None, before: str | None = None):
    """
    Get the Repovul items that match a project's repo URL.

    The list can be filtered with the optional `after` and `before` parameters, which must be ISO
    8601 dates.

    `after` is included, and `before` is excluded, i.e. the possible options are: (1) after <= date,
    (2) after <= date < before, (3) date < before.
    """
    engine = create_engine(f"sqlite:///{Config.paths.db}/repovul.db")
    with Session(engine) as session:
        query = select(RepovulItem).where(RepovulItem.repo_url == repo_url)

        if after:
            start_date = datetime.fromisoformat(after)
            query = query.where(RepovulItem.published >= start_date)
        if before:
            end_date = datetime.fromisoformat(before)
            query = query.where(RepovulItem.published < end_date)

        results = session.exec(query).all()
        results_json = [item.to_dict() for item in results]
        print(json.dumps(results_json, indent=2))


def get_commits(after: str | None = None, before: str | None = None, project: str | None = None):
    """
    Get a list of all commits that have at least one vuln within the date range.

    The list can be filtered with the optional `after` and `before` parameters, which must be ISO
    8601 dates.

    `after` is included, and `before` is excluded, i.e. the possible options are: (1) after <= date,
    (2) after <= date < before, (3) date < before.

    The list can also be filtered by the `project` parameter, a repo URL.
    """
    engine = create_engine(f"sqlite:///{Config.paths.db}/repovul.db")
    with Session(engine) as session:
        query = select(RepovulItem)
        if project:
            query = query.where(RepovulItem.repo_url == project)
        if after:
            start_date = datetime.fromisoformat(after)
            query = query.where(RepovulItem.published >= start_date)
        if before:
            end_date = datetime.fromisoformat(before)
            query = query.where(RepovulItem.published < end_date)

        results = session.exec(query).all()
        commits = {commit for item in results for commit in item.commits}
        print(json.dumps(list(commits), indent=2))


def json_export() -> None:
    """Export the contents of the SQL database into JSON files in the data directory."""
    if Path(Config.paths.data).exists():
        print(f"The data directory already exists at {Config.paths.data}.")
        print("Please remove it or back it up before exporting.")
        sys.exit(1)
    for path in [Config.paths.repovul_vulns, Config.paths.repovul_revisions]:
        path.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{Config.paths.db}/repovul.db")
    with Session(engine) as session:
        item_query = select(RepovulItem)
        repovul_items = session.exec(item_query).all()
        for item in repovul_items:
            item.log()
        revision_query = select(RepovulRevision)
        repovul_revisions = session.exec(revision_query).all()
        for revision in repovul_revisions:
            revision.log()
    print(
        f"Successfully exported {len(repovul_items)} RepovulItems and {len(repovul_revisions)} RepovulRevisions to {Config.paths.data}."
    )


def json_import() -> None:
    """Import the contents of the JSON files in the data directory into the SQL database."""
    if Path(Config.paths.db).exists():
        print(f"The database directory already exists at {Config.paths.db}.")
        print("Please remove it or back it up before importing.")
        sys.exit(1)
    Path(Config.paths.db).mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{Config.paths.db}/repovul.db")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        repovul_item_files = list(Config.paths.repovul_vulns.glob("*/*.json"))
        for path in repovul_item_files:
            item = RepovulItem.from_file(path)
            session.add(item)
        repovul_revision_files = list(Config.paths.repovul_revisions.glob("*/*.json"))
        for path in repovul_revision_files:
            revision = RepovulRevision.from_file(path)
            session.add(revision)
        session.commit()
    print(
        f"Successfully imported {len(repovul_item_files)} RepovulItems and {len(repovul_revision_files)} RepovulRevisions into the database at {Config.paths.db}."
    )


def main():
    fire.Fire(
        {
            "download": download,
            "convert_one": convert_one,
            "convert_all": convert_all,
            "convert_range": convert_range,
            "get_by_commit": get_by_commit,
            "get_by_project": get_by_project,
            "get_commits": get_commits,
            "get_projects": get_projects,
            "json_export": json_export,
            "json_import": json_import,
        }
    )


def profile():
    pr = cProfile.Profile()
    pr.enable()
    try:
        main()
    finally:
        pr.disable()
        pr.dump_stats("profile.stats")

        p = pstats.Stats("profile.stats")
        p.sort_stats("cumtime").print_stats(50)


if __name__ == "__main__":
    main()
