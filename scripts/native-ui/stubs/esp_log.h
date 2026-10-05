#include <stdio.h>
#define ESP_LOGI(tag,fmt,...) printf(fmt "\n", ##__VA_ARGS__)
