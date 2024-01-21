from flask import Flask, jsonify, render_template, request

app = Flask(__name__)

supermarket_jewelries = {
    'necklaces': ['Necklace 1', 'Necklace 2'],
    'bracelets': ['Bracelet A', 'Bracelet B'],
    'earrings': ['Earring X', 'Earring Y'],
}

@app.route('/')
def index():
    return render_template('supermarket.html', jewelries=supermarket_jewelries)

@app.route('/add_jewelry', methods=['POST'])
def add_jewelry():
    data = request.json
    category = data['category']
    new_jewelry = data['jewelry']

    supermarket_jewelries[category].append(new_jewelry)

    return jsonify({'success': True})

if __name__ == '__main__':
    app.run(debug=True)