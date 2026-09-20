from .models import Product


class _ProductIdSet:
    session_key = None
    max_items = None

    def __init__(self, request):
        self.session = request.session
        ids = self.session.get(self.session_key)
        if ids is None:
            ids = self.session[self.session_key] = {}
        self.ids = ids

    def save(self):
        self.session[self.session_key] = self.ids
        self.session.modified = True

    def contains(self, product_id):
        return str(product_id) in self.ids

    def toggle(self, product_id):
        product_id = str(product_id)
        if product_id in self.ids:
            del self.ids[product_id]
            added = False
        else:
            if self.max_items and len(self.ids) >= self.max_items:
                oldest = next(iter(self.ids))
                del self.ids[oldest]
            self.ids[product_id] = True
            added = True
        self.save()
        return added

    def remove(self, product_id):
        self.ids.pop(str(product_id), None)
        self.save()

    def clear(self):
        self.session[self.session_key] = {}
        self.save()

    def count(self):
        return len(self.ids)

    def products(self):
        # Annotated so the cards on the wishlist/compare pages get their review
        # numbers in this one query rather than one each.
        products = Product.objects.filter(id__in=self.ids.keys()).with_rating_stats()
        products_by_id = {str(p.id): p for p in products}
        return [products_by_id[pid] for pid in self.ids if pid in products_by_id]


class Wishlist(_ProductIdSet):
    session_key = 'wishlist'
    max_items = None


class CompareList(_ProductIdSet):
    session_key = 'compare'
    max_items = 4
