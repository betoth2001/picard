# -*- coding: utf-8 -*-
#
# Picard, the next-generation MusicBrainz tagger
# Copyright (C) 2004 Robert Kaye
# Copyright (C) 2006 Lukáš Lalinský
#
# This program is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License
# as published by the Free Software Foundation; either version 2
# of the License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.

from PyQt4 import QtCore, QtGui
from picard.album import Album
from picard.cluster import Cluster
from picard.track import Track
from picard.file import File
from picard.config import TextOption, BoolOption
from picard.util import partial
from picard.util.tags import display_tag_name
from picard.ui.edittagdialog import EditTagDialog


class TagCounter(dict):

    def __init__(self):
        self.counts = {}
        self.different = set()
        self.objects = 0

    def __getitem__(self, tag):
        return dict.get(self, tag, [])

    def add(self, tag, values):
        if tag not in self.different:
            values = sorted(values)
            if tag not in self:
                self[tag] = values
            elif self[tag] != values:
                self.different.add(tag)
                self[tag] = None
        self.counts[tag] = self.counts.get(tag, 0) + 1

    def clear(self):
        dict.clear(self)
        self.counts.clear()
        self.different.clear()
        self.objects = 0
        return self

    def different_placeholder(self, tag):
        count = self.counts.get(tag, 0)
        missing = self.objects - count
        if tag in self.different or (count > 0 and missing > 0):
            if missing > 0:
                return ungettext("(missing from %d item)", "(missing from %d items)", missing) % missing
            else:
                return _("(different across %d items)") % self.objects
        return None


class MetadataBox(QtGui.QTableWidget):

    options = (
        TextOption("persist", "metadata_box_sizes", "150 300 300"),
        BoolOption("persist", "show_changes_first", False)
    )

    common_tags = (
        "title",
        "artist",
        "album",
        "tracknumber",
        "~length",
        "date",
    )

    def __init__(self, parent):
        QtGui.QTableWidget.__init__(self, parent)
        self.parent = parent
        self.setColumnCount(3)
        self.setHorizontalHeaderLabels((N_("Tag"), N_("Original Value"), N_("New Value")))
        self.horizontalHeader().setStretchLastSection(True)
        self.horizontalHeader().setClickable(False)
        self.verticalHeader().setDefaultSectionSize(21)
        self.verticalHeader().setVisible(False)
        self.setSelectionMode(QtGui.QAbstractItemView.SingleSelection)
        self.setStyleSheet("QTableWidget {border: none;}")
        self.itemChanged.connect(self.item_changed)
        self._item_signals = True
        self.colors = {
            "default": self.palette().color(QtGui.QPalette.Text),
            "removed": QtGui.QBrush(QtGui.QColor("red")),
            "added": QtGui.QBrush(QtGui.QColor("green")),
            "changed": QtGui.QBrush(QtGui.QColor("darkgoldenrod")),
        }
        self.files = set()
        self.tracks = set()
        self.objects = set()
        self.orig_tags = TagCounter()
        self.new_tags = TagCounter()
        self.selection_mutex = QtCore.QMutex()
        self.updating = False
        self.update_pending = False
        self.selection_dirty = False
        self._editing = None # Reference to QTableWidgetItem being edited
        self.add_tag_action = QtGui.QAction(_(u"Add New Tag..."), parent)
        self.add_tag_action.triggered.connect(partial(self.edit_tag, ""))
        self.changes_first_action = QtGui.QAction(_(u"Show Changes First"), parent)
        self.changes_first_action.setCheckable(True)
        self.changes_first_action.setChecked(self.config.persist["show_changes_first"])
        self.changes_first_action.toggled.connect(self.toggle_changes_first)

    def edit(self, index, trigger, event):
        if index.column() != 2:
            return False
        if trigger in (QtGui.QAbstractItemView.DoubleClicked,
                       QtGui.QAbstractItemView.EditKeyPressed,
                       QtGui.QAbstractItemView.AnyKeyPressed):
            item = self.itemFromIndex(index)
            tag = self.tag_names[item.row()]
            values = self.new_tags[tag]
            if len(values) > 1:
                self.edit_tag(tag)
                return False
            else:
                self._editing = item
                return QtGui.QTableWidget.edit(self, index, trigger, event)
        return False

    def event(self, event):
        if (event.type() == QtCore.QEvent.KeyPress and
            event.modifiers() == QtCore.Qt.ControlModifier and
            event.key() == QtCore.Qt.Key_V):
            item = self.currentItem()
            if item.column() == 2 and self.tag_names[item.row()] != "~length":
                item.setText(self.tagger.clipboard().text())
        return QtGui.QTableWidget.event(self, event)

    def closeEditor(self, editor, hint):
        self._editing = None
        QtGui.QTableWidget.closeEditor(self, editor, hint)

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos())
        if not item:
            return
        menu = QtGui.QMenu(self)
        tag = self.tag_names[item.row()]
        if tag != "~length":
            column = item.column()
            if column == 1:
                if self.tag_status(tag) == "changed" and tag not in self.new_tags.different:
                    copy_to_new_action = QtGui.QAction(_(u"Copy to New Value"), self.parent)
                    copy_to_new_action.triggered.connect(partial(self.copy_to_new, tag))
                    menu.addAction(copy_to_new_action)
                    menu.addSeparator()
            elif column == 2:
                edit_tag_action = QtGui.QAction(_(u"Edit..."), self.parent)
                edit_tag_action.triggered.connect(partial(self.edit_tag, tag))
                menu.addAction(edit_tag_action)
                if self.tag_status(tag) != "removed":
                    remove_tag_action = QtGui.QAction(_(u"Remove"), self.parent)
                    remove_tag_action.triggered.connect(partial(self.remove_tag, tag))
                    menu.addAction(remove_tag_action)
                menu.addSeparator()
        menu.addAction(self.add_tag_action)
        menu.addSeparator()
        menu.addAction(self.changes_first_action)
        menu.exec_(event.globalPos())
        event.accept()

    def edit_tag(self, tag):
        EditTagDialog(self.parent, tag).exec_()

    def toggle_changes_first(self, checked):
        self.config.persist["show_changes_first"] = checked
        self.update()

    def set_tag_values(self, tag, values):
        self.parent.ignore_selection_changes = True
        self.new_tags[tag] = values
        self.new_tags.different.discard(tag)
        for obj in self.objects:
            obj.metadata._items[tag] = values
            obj.update()
        self.update()
        self.parent.ignore_selection_changes = False

    def copy_to_new(self, tag):
        self.set_tag_values(tag, list(self.orig_tags[tag]))

    def remove_tag(self, tag):
        self.set_tag_values(tag, [])

    def update_selection(self):
        self.selection_mutex.lock()
        self.selection_dirty = True
        self.selection_mutex.unlock()

    def _update_selection(self):
        files = self.files
        tracks = self.tracks
        objects = self.objects
        files.clear()
        tracks.clear()
        objects.clear()
        for obj in self.parent.panel._selected_objects:
            if isinstance(obj, File):
                files.add(obj)
            elif isinstance(obj, Track):
                tracks.add(obj)
                files.update(obj.linked_files)
            elif isinstance(obj, Cluster) and obj.can_edit_tags():
                objects.add(obj)
                files.update(obj.files)
            elif isinstance(obj, Album):
                objects.add(obj)
                tracks.update(obj.tracks)
                for track in obj.tracks:
                    files.update(track.linked_files)
        objects.update(files)
        objects.update(tracks)
        self.selection_dirty = False

    def update(self):
        if not self.updating:
            self.updating = True
            self.update_pending = False
            self.tagger.other_queue.put((self._update_tags, self._update_items, QtCore.Qt.LowEventPriority))
        else:
            self.update_pending = True

    def _update_tags(self):
        self.selection_mutex.lock()
        if self.selection_dirty:
            self._update_selection()
        self.selection_mutex.unlock()

        if not (self.files or self.tracks):
            return None
        orig_tags = self.orig_tags.clear()
        new_tags = self.new_tags.clear()
        # existing_tags are orig_tags that would not be overwritten by
        # any new_tags, assuming clear_existing_tags is disabled.
        existing_tags = set()

        clear_existing_tags = self.config.setting["clear_existing_tags"]

        for file in self.files:
            for name, values in file.metadata._items.iteritems():
                if not name.startswith("~") or name == "~length":
                    new_tags.add(name, values)
            for name, values in file.orig_metadata._items.iteritems():
                if not name.startswith("~") or name == "~length":
                    orig_tags.add(name, values)
                    if not ((name in new_tags and not name in existing_tags) or clear_existing_tags):
                        new_tags.add(name, values)
                        existing_tags.add(name)
            orig_tags.objects += 1

        new_tags.objects = orig_tags.objects
        for track in self.tracks:
            if track.num_linked_files == 0:
                for name, values in track.metadata._items.iteritems():
                    if not name.startswith("~") or name == "~length":
                        new_tags.add(name, values)
                new_tags.objects += 1

        all_tags = set(orig_tags.keys() + new_tags.keys())
        common_tags = MetadataBox.common_tags
        tag_names = [t for t in common_tags if t in all_tags] + sorted(all_tags.difference(common_tags))

        if self.config.persist["show_changes_first"]:
            self.tag_names = []
            tags_by_status = {}
            for tag in tag_names:
                tags_by_status.setdefault(self.tag_status(tag), []).append(tag)
            for status in ("changed", "added", "removed", "default"):
                self.tag_names += tags_by_status.pop(status, [])
        else:
            self.tag_names = [tag for tag in tag_names if self.tag_status(tag) != "empty"]
        return True

    def _update_items(self, result=None, error=None):
        if result is None or error is not None:
            self.orig_tags.clear()
            self.new_tags.clear()
            self.tag_names = None
            self.setRowCount(0)
            self.updating = False
            if self.update_pending:
                self.update()
            return

        self._item_signals = False
        self.setRowCount(len(self.tag_names))
        flags = QtCore.Qt.ItemIsSelectable | QtCore.Qt.ItemIsEnabled

        for i, name in enumerate(self.tag_names):
            tag_item = self.item(i, 0)
            orig_item = self.item(i, 1)
            new_item = self.item(i, 2)
            if not tag_item:
                tag_item = QtGui.QTableWidgetItem()
                tag_item.setFlags(QtCore.Qt.ItemIsEnabled)
                font = tag_item.font()
                font.setBold(True)
                tag_item.setFont(font)
                self.setItem(i, 0, tag_item)
            if not orig_item:
                orig_item = QtGui.QTableWidgetItem()
                orig_item.setFlags(flags)
                self.setItem(i, 1, orig_item)
            if not new_item:
                new_item = QtGui.QTableWidgetItem()
                self.setItem(i, 2, new_item)
            new_item.setFlags(flags if name == "~length" else flags | QtCore.Qt.ItemIsEditable)
            tag_item.setText(display_tag_name(name))
            self.set_item_value(orig_item, self.orig_tags, name)
            if new_item != self._editing:
                self.set_item_value(new_item, self.new_tags, name)
                self.set_row_colors(i)

        self._item_signals = True
        self.updating = False
        if self.update_pending:
            self.update()

    def set_item_value(self, item, tags, name):
        different = tags.different_placeholder(name)
        item.setText(different if different else "; ".join(tags[name]))
        font = item.font()
        font.setItalic(bool(different))
        item.setFont(font)

    def set_row_colors(self, row):
        status = self.tag_status(self.tag_names[row])
        if status in ("removed", "changed", "default"):
            self.item(row, 1).setForeground(self.colors[status])
        if status in ("added", "changed", "default"):
            self.item(row, 2).setForeground(self.colors[status])

    def tag_status(self, tag):
        orig_values = self.orig_tags[tag]
        new_values = self.new_tags[tag]
        orig_empty = not (orig_values or tag in self.orig_tags.different)
        new_empty = not (new_values or tag in self.new_tags.different)
        if new_empty and not orig_empty:
            return "removed"
        elif orig_empty and not new_empty:
            return "added"
        elif not (orig_empty or new_empty) and orig_values != new_values:
            return "changed"
        elif orig_empty and new_empty:
            return "empty"
        else:
            return "default"

    def item_changed(self, item):
        if not self._item_signals:
            return
        self._item_signals = False
        tag = self.tag_names[item.row()]
        value = unicode(item.text())
        value = [value] if value else []
        self.new_tags[tag] = value
        self.new_tags.different.discard(tag)
        font = item.font()
        font.setItalic(False)
        item.setFont(font)
        self.set_row_colors(item.row())
        self.parent.ignore_selection_changes = True
        for obj in self.objects:
            obj.metadata._items[tag] = value
            obj.update()
        if self.config.persist["show_changes_first"]:
            self.update()
        self.parent.ignore_selection_changes = False
        self._item_signals = True

    def restore_state(self):
        sizes = self.config.persist["metadata_box_sizes"].split(" ")
        header = self.horizontalHeader()
        try:
            for i in range(header.count()):
                size = max(int(sizes[i]), header.sectionSizeHint(i))
                header.resizeSection(i, size)
        except IndexError:
            pass
        self.shrink_columns()

    def save_state(self):
        sizes = []
        header = self.horizontalHeader()
        for i in range(header.count()):
            sizes.append(str(header.sectionSize(i)))
        self.config.persist["metadata_box_sizes"] = " ".join(sizes)

    def shrink_columns(self):
        header = self.horizontalHeader()
        cols = [header.sectionSize(i) for i in range(3)]
        width = sum(cols)
        visible_width = self.contentsRect().width()
        scroll = self.verticalScrollBar()
        if scroll.isVisible():
            visible_width -= scroll.width()
        if width > visible_width:
            diff = float(width - visible_width)
            for i in range(3):
                sub = int(diff * cols[i] / width) + 1
                header.resizeSection(i, cols[i] - sub)
