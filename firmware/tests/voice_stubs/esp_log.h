#pragma once
static inline void voice_test_log(const char *tag,const char *format,...){(void)tag;(void)format;}
#define ESP_LOGI(...) voice_test_log(__VA_ARGS__)
