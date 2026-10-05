#pragma once
#include <CommonCrypto/CommonDigest.h>
#include <stddef.h>
static inline int mbedtls_sha256(const unsigned char *input,size_t length,unsigned char output[32],int is224)
{if(is224)return -1;CC_SHA256(input,(CC_LONG)length,output);return 0;}
