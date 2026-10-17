if [ -n "$BASH_VERSION" ] && [ -x /usr/local/bin/de-note-command ]; then
    de_note_last_command() {
        de_last=`history 1 2>/dev/null | sed 's/^ *[0-9]* *//'`
        if [ -n "$de_last" ] && [ "$de_last" != "$de_previous" ]; then
            de_previous="$de_last"
            /usr/local/bin/de-note-command "$de_last" >/dev/null 2>&1
        fi
    }
    case "$PROMPT_COMMAND" in
        *de_note_last_command*) ;;
        "") PROMPT_COMMAND="de_note_last_command" ;;
        *) PROMPT_COMMAND="de_note_last_command; $PROMPT_COMMAND" ;;
    esac
fi
