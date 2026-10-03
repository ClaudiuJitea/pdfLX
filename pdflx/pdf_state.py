"""Rollback snapshots that preserve the live Document and canvas object identities."""
import copy

COLLECTIONS = ('editable_texts', 'editable_shapes', 'editable_images', 'editable_strokes')


class PdfState:
    def __init__(self, window):
        from . import pdf_handler
        self.doc = window.doc
        self.objects = {}
        for xref in range(1,self.doc.xref_length()):
            try:
                self.objects[xref] = (self.doc.xref_object(xref,compressed=False),
                    self.doc.xref_stream(xref) if self.doc.xref_is_stream(xref) else None)
            except RuntimeError:
                # MuPDF reports vacated xrefs (after rollback) as missing objects.
                self.objects[xref] = ('null',None)
        self.trailer = {key: self.doc.xref_get_key(-1, key)[1] for key in ('Root', 'Info')}
        self.collections = {name: list(getattr(window, name, [])) for name in COLLECTIONS}
        self.pages = {page: tuple(list(group) for group in groups) for page, groups in
                      getattr(getattr(window, '_active_session', None), 'page_objects', {}).items()}
        members = list({id(obj): obj for values in self.collections.values() for obj in values}.values())
        for groups in self.pages.values():
            members.extend(obj for values in groups for obj in values if all(obj is not old for old in members))
        self.models = [(obj, copy.deepcopy(obj.__dict__)) for obj in members]
        self.page_index = window.current_page_index
        self.baselines = {key[1]: value for key, value in pdf_handler._page_snapshots.items() if key[0] == id(self.doc)}
        self.links = {key[1]: copy.deepcopy(value) for key, value in pdf_handler._page_original_links.items() if key[0] == id(self.doc)}
        self.modified = getattr(window, 'document_modified', False)

    def restore(self, window):
        from . import pdf_handler
        if window.doc is not self.doc:
            raise ValueError('The document for this history entry is no longer active.')
        for xref in range(1, self.doc.xref_length()):
            obj, stream = self.objects.get(xref, ('null', None))
            self.doc.update_object(xref, obj)
            if stream is not None:
                self.doc.update_stream(xref, stream)
        for key, value in self.trailer.items():
            self.doc.xref_set_key(-1, key, value)
        self.doc.init_doc()
        # Invalidate all wrappers, including ones held by a caller. reload_page
        # cannot do this reliably when multiple wrappers share a native page.
        self.doc._reset_page_refs()
        for obj, state in self.models:
            obj.__dict__.clear()
            obj.__dict__.update(copy.deepcopy(state))
        session = getattr(window, '_active_session', None)
        if session is not None:
            session.page_objects = {page: tuple(list(group) for group in groups) for page, groups in self.pages.items()}
            self.doc.editor_page_models = session.page_objects
        for name, values in self.collections.items():
            setattr(window, name, list(values))
        window.current_page_index = min(self.page_index, self.doc.page_count-1)
        if session is not None and window.current_page_index in session.page_objects:
            for name, values in zip(COLLECTIONS, session.page_objects[window.current_page_index]):
                setattr(window, name, values)
        pdf_handler.release_page_snapshots(self.doc)
        for page, baseline in self.baselines.items():
            pdf_handler._page_snapshots[id(self.doc), page] = baseline
        for page, links in self.links.items():
            pdf_handler._page_original_links[id(self.doc), page] = copy.deepcopy(links)
        pdf_handler.invalidate_page_cache(self.doc)
        window.document_modified = self.modified
