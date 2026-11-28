# Preserve the first chat error and its retry identity

Goal: make F7's safe HTTP response visible when the first send in a new chat fails, while retaining the original draft and business retry ticket.

The actual frozen image returned the expected safe500 and transport ID. Browser verification then found that the new chat immediately navigated to its freshly created session even on failure. ChatWorkspace keys Conversation by session ID, so navigation unmounted the component which owned perform's error state. The generic alert disappeared. This preexisting behavior undermined the approved F7 error-feedback path.

The smallest fix retains navigation after successful sends. On failure an existing session still refreshes its durable state before rethrowing to perform. A failed new session remains in the current component, which already owns createdSession, pendingSend and the draft. The existing alert renders the safe message. A manual retry with identical content reuses the same created session and business request ID. No automatic retry, provider invocation, new UI component or successful JSON rewriting is introduced.

A React test with the actual memory router reproduces the original `/chat/7` versus expected `/chat` mismatch. With the correction, it observes the alert, unchanged draft, one created session and the same ticket across a failed first send and successful retry. The safe HTTP body/header ID remains a separate transport identifier. The final image/browser run is a separate gate from the controlled React test.
