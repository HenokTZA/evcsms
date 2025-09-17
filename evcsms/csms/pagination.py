from rest_framework.pagination import PageNumberPagination

class StandardResultsSetPagination(PageNumberPagination):
    page_size = 30                  # default page size
    page_size_query_param = "page_size"  # allow ?page_size=50 (optional)
    max_page_size = 100

