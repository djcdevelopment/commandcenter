# Corpus Guard System

This project implements a corpus guard system for managing beliefs as described in the instructions.

## Overview

The corpus guard system provides a way to store and retrieve beliefs from findings.json, with validation to ensure only valid beliefs are stored.

## Features

- Store beliefs in findings.json
- Validate beliefs before storage
- Reject invalid beliefs
- Retrieve all stored beliefs
- Clear all beliefs

## Usage

```python
from corpus_guard import CorpusGuard

# Create a corpus guard instance
guard = CorpusGuard()

# Store valid beliefs
guard.store_belief({"belief": "The sky is blue"})

# Store invalid beliefs (these will be rejected)
result = guard.store_belief({"belief": ""})  # Returns False

# Retrieve all beliefs
all_beliefs = guard.get_all_beliefs()
```

## Implementation Details

The system ensures that only beliefs with a non-empty 'belief' key are stored. Invalid beliefs (those missing the 'belief' key or having empty content) are rejected and logged.

## File Structure

- `corpus_guard.py`: Main implementation file
- `findings.json`: Storage file for beliefs (created automatically)
