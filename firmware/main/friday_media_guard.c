#include "friday_media_guard.h"
#include <string.h>
#include <math.h>
void friday_media_clear(friday_media_guard_t *g) { memset(g, 0, sizeof(*g)); }
bool friday_media_begin(friday_media_guard_t *g, const char *request)
{
    friday_media_clear(g);
    if (request == NULL || request[0] == '\0' || strlen(request) > 128) return false;
    memcpy(g->request, request, strlen(request) + 1); return true;
}
bool friday_media_accept(friday_media_guard_t *g, const char *request, const char *session, double sequence)
{
    if (request == NULL || session == NULL || g->request[0] == '\0' || strcmp(request, g->request) != 0 ||
        strlen(session) != 24 || strspn(session,"0123456789abcdef") != 24 || !isfinite(sequence) ||
        sequence < 1 || sequence > 9007199254740991.0 || floor(sequence) != sequence ||
        (uint64_t)sequence <= g->sequence || (g->session[0] != '\0' && strcmp(session, g->session) != 0)) return false;
    memcpy(g->session, session, 25); g->sequence = (uint64_t)sequence; return true;
}
