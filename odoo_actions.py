def create_customer(client, customer_data):
    """Creates a new customer and returns its ID."""
    print(f"-> Creating Customer/Contact: {customer_data.get('name')}...")
    customer_id = client.create('res.partner', customer_data)
    print(f"   ID: {customer_id}")
    return customer_id

def create_product(client, product_data):
    """Creates a new product and returns its ID."""
    print(f"-> Creating Product: {product_data.get('name')}...")
    product_id = client.create('product.product', product_data)
    print(f"   ID: {product_id}")
    return product_id

def create_sale_order(client, order_data):
    """Creates a new sale order and returns its ID."""
    print("-> Creating Sale Order...")
    order_id = client.create('sale.order', order_data)
    print(f"   ID: {order_id}")
    return order_id

def get_country_id(client, country_code):
    """Finds the Odoo database ID for a given country code (e.g., 'DE')."""
    country_info = client.search_read(
        'res.country',
        [["code", "=", country_code.upper()]],
        fields=["id"],
        limit=1,
    )
    if country_info:
        return country_info[0]['id']
    return False # Return False if country not found