"""The session list widget."""
from __future__ import annotations


from textual.binding import Binding
from textual.widgets import OptionList
from textual.widgets.option_list import Option



class SessionList(OptionList):
    BINDINGS = [
        Binding("j", "cursor_down", show=False),
        Binding("k", "cursor_up", show=False),
        # Here rather than on the app, so tab still completes paths in dialogs.
        Binding("tab", "app.next_waiting", "Next waiting", show=False),
        Binding("shift+tab", "app.next_waiting(True)", show=False),
    ]
    # Rows of sessions open beside the list get a soft background across the whole row.
    COMPONENT_CLASSES = {"session-list--open"}
    DEFAULT_CSS = """
    /* Fixed greys: the ansi themes' colors come from the terminal and can't be mixed into a tint. */
    SessionList:light > .session-list--open { background: #eeeeee; }
    SessionList:dark > .session-list--open { background: #333333; }
    SessionList:light > .option-list--option-hover { background: #e6e6e6; }
    SessionList:dark > .option-list--option-hover { background: #3a3a3a; }
    /* Away in a session pane, the cursor fades to a grey; the list's own focus keeps it solid. */
    SessionList:blur > .option-list--option-highlighted { color: $foreground; text-style: none; }
    SessionList:light:blur > .option-list--option-highlighted { background: #dcdcdc; }
    SessionList:dark:blur > .option-list--option-highlighted { background: #444444; }
    """
    open_ids: set[str] = set()

    def _get_option_render(self, option: Option, style):
        index = self._option_to_index.get(option)
        if option.id in self.open_ids and index != self.highlighted and index != self._mouse_hovering_over:
            style = self.get_visual_style("option-list--option", "session-list--open")
        return super()._get_option_render(option, style)
