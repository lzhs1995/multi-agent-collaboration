Exact OfficeCLI Word revision 0.1.0.dev5 fixture

These two source files preserve exact installed release bytes from commit c68cc8aec9aacfb09462a7bd8b474bc0be83a3c8. See PROVENANCE.json for byte/SHA pins. LICENSE was obtained with git show c68cc8aec9aacfb09462a7bd8b474bc0be83a3c8:LICENSE (MIT), not the working tree.

Tests patch only the fixed module constants to this fixture directory and a temporary named osascript executable. They execute the original Python service and real temporary filesystem/flock behavior; actual Apple Events and validators are replaced with owned Python fixtures. Production accepts only its fixed installed service path/SHA and /usr/bin/osascript.
