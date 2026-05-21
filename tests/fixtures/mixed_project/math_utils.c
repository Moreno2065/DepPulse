#include "math_utils.h"
#include "logger.h"
#include <stdio.h>

// Block comment test: #include "fake.h"
// Another // line comment: #include "fake2.h"

int main() {
    int result = add(3, 4);
    log_message("Result: %d", result);
    printf("3 + 4 = %d\n", result);
    return 0;
}
