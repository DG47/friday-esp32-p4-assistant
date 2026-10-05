#include <assert.h>
#include <math.h>
#include "friday_media_guard.h"
int main(void) {
    friday_media_guard_t g = {0};
    const char *s="0123456789abcdef01234567", *other="1123456789abcdef01234567";
    assert(!friday_media_accept(&g,"a",s,1));
    assert(friday_media_begin(&g,"a"));
    assert(friday_media_accept(&g,"a",s,1));
    assert(!friday_media_accept(&g,"a",s,1));
    assert(!friday_media_accept(&g,"a",s,0));
    assert(!friday_media_accept(&g,"a",other,2));
    assert(!friday_media_accept(&g,"a",s,NAN));
    assert(!friday_media_accept(&g,"a",s,2.5));
    assert(friday_media_accept(&g,"a",s,3));
    friday_media_clear(&g); /* local dismiss / expiry / replacement / disconnect */
    assert(!friday_media_accept(&g,"a",s,4));
    assert(friday_media_begin(&g,"b")); /* rapid reentry: old frame in flight */
    assert(!friday_media_accept(&g,"a",s,5));
    assert(friday_media_accept(&g,"b",other,1));
    friday_media_clear(&g);
    assert(!friday_media_accept(&g,"b",other,2));
    assert(friday_media_begin(&g,"c")); /* reconnect requires new request */
    assert(!friday_media_accept(&g,"b",other,3));
    assert(friday_media_accept(&g,"c",s,1));
    return 0;
}
