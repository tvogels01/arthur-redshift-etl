#! /bin/bash

# Look here for inspiration: https://github.com/scop/bash-completion

_arthur_completion()
{

    local cur prev opts
    cur="${COMP_WORDS[COMP_CWORD]}"
    prev="${COMP_WORDS[COMP_CWORD-1]}"

    case "$prev" in
        "arthur.py")
            opts="initialize create_user design auto_design sync extract load upgrade update unload
                  restore_schemas validate explain ls ping show_dependents show_pipelines selftest
                  --submit --config"
            COMPREPLY=( $(compgen -W "$opts" -- "$cur") )
            ;;
        "-c"|"--config")
            opts=$(find -L  . -maxdepth 2 -name '*.yaml' -or -name '*.sh' | sed -e 's:^\./::')
            COMPREPLY=( $(compgen -W "$opts" -d -- "$cur") )
            ;;
        "auto_design")
            COMPREPLY=( $(compgen -W "CTAS VIEW" -- "$cur") )
            ;;
        "selftest")
            COMPREPLY=( $(compgen -W "all doctest typecheck" -- "$cur") )
            ;;
        *)
            case "$cur" in
                *.*)
                    opts=$(find -L schemas -type f -name '*.yaml' 2>/dev/null |
                           sed -e 's:schemas/\([^/]*\)/[^-]*-\([^.]*\).yaml:\1.\2:')
                    ;;
                *)
                    opts=$(find -L schemas -type f -name '*.yaml' 2>/dev/null |
                           sed -e 's:schemas/\([^/]*\)/[^-]*-\([^.]*\).yaml:\1:' | uniq)
                    ;;
            esac
            COMPREPLY=( $(compgen -W "$opts" -- "$cur") )
            ;;
    esac

} &&
complete -F _arthur_completion arthur.py
