if [ -n "$BASH_VERSION" ] && [ -x /usr/local/bin/de-note-command ]; then
    de_note_last_command() {
        de_entry=$(HISTTIMEFORMAT= builtin history 1 2>/dev/null)
        if [ -n "$de_entry" ] && [ "$de_entry" != "$de_previous" ]; then
            de_previous="$de_entry"
            de_last=$(printf '%s\n' "$de_entry" | sed '1s/^ *[0-9]* *//')
            /usr/local/bin/de-note-command "$de_last" >/dev/null 2>&1
        fi
    }
    case "$PROMPT_COMMAND" in
        *de_note_last_command*) ;;
        "") PROMPT_COMMAND="de_note_last_command" ;;
        *) PROMPT_COMMAND="de_note_last_command; $PROMPT_COMMAND" ;;
    esac
fi
