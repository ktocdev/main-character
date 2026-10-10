# SPDX-License-Identifier: AGPL-3.0-or-later
"""
One writer at a time on the entity graph. A re-extract runs for minutes in
the background beside the close pipeline and hand edits; without a shared
lock a split could read a raw file, the re-extract overwrite it, and the
split write the old copy back, losing one of them without a word.
"""
import threading

from fastapi.routing import APIRoute

import entities
import server


def test_a_locked_endpoint_waits_for_the_writer_ahead_of_it():
    ran = threading.Event()
    endpoint = server._entity_write(lambda: ran.set())
    with entities.WRITE_LOCK:
        t = threading.Thread(target=endpoint)
        t.start()
        assert not ran.wait(0.2)  # held elsewhere: it waits
    t.join(2)
    assert ran.is_set()


def test_the_lock_is_reentrant_so_an_edit_can_rebuild_inside_it():
    # an edit endpoint holds the lock and then calls _rebuild -> build
    assert server._entity_write(lambda: server._entity_write(lambda: "ok")())() == "ok"


def test_every_entity_and_group_write_holds_the_lock():
    writes = {"/api/observation", "/api/undo", "/api/redo"}
    reads = {"/api/entities/suggest", "/api/entities/suggest-things", "/api/entities/suggest-parts",
             "/api/entities/reextract/preview",
             # only schedules the background run, whose writes take the lock
             "/api/entities/reextract"}
    unlocked = [r.path for r in server.app.routes
                if isinstance(r, APIRoute) and "POST" in r.methods
                and (r.path.startswith(("/api/entities", "/api/groups")) or r.path in writes)
                and r.path not in reads
                and getattr(r.endpoint, "__wrapped__", None) is None]
    assert unlocked == []
