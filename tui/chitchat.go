package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"
)

var chitchatBase = getEnv("CHITCHAT_URL", "http://100.126.64.13:19999")

type ChatMessage struct {
	TS    string `json:"ts"`
	From  string `json:"from"`
	Text  string `json:"text"`
	Topic string `json:"topic"`
	Room  string `json:"room"`
	Type  string `json:"type"`
}

type RoomInfo struct {
	Name         string `json:"name"`
	MessageCount int    `json:"message_count"`
}

type ChitchatClient struct {
	mu       sync.RWMutex
	rooms    []string
	messages map[string][]ChatMessage
	client   *http.Client
}

func NewChitchatClient() *ChitchatClient {
	return &ChitchatClient{
		rooms:    []string{"general"},
		messages: make(map[string][]ChatMessage),
		client:   &http.Client{Timeout: 5 * time.Second},
	}
}

func (c *ChitchatClient) Rooms() ([]RoomInfo, error) {
	resp, err := c.client.Get(chitchatBase + "/rooms")
	if err != nil {
		return nil, fmt.Errorf("rooms fetch: %w", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	var result struct {
		Rooms []RoomInfo `json:"rooms"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("rooms decode: %w", err)
	}
	c.mu.Lock()
	c.rooms = nil
	for _, r := range result.Rooms {
		c.rooms = append(c.rooms, r.Name)
	}
	c.mu.Unlock()
	return result.Rooms, nil
}

func (c *ChitchatClient) History(room string, limit int) ([]ChatMessage, error) {
	u := fmt.Sprintf("%s/%s/history", chitchatBase, url.PathEscape(room))
	if limit > 0 {
		u += fmt.Sprintf("?limit=%d", limit)
	}
	resp, err := c.client.Get(u)
	if err != nil {
		return nil, fmt.Errorf("history fetch: %w", err)
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	var result struct {
		Messages []ChatMessage `json:"messages"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return nil, fmt.Errorf("history decode: %w", err)
	}
	c.mu.Lock()
	c.messages[room] = result.Messages
	c.mu.Unlock()
	return result.Messages, nil
}

func (c *ChitchatClient) Say(room, fromName, text string) error {
	payload := map[string]string{
		"text":      text,
		"from_name": fromName,
	}
	data, _ := json.Marshal(payload)
	resp, err := c.client.Post(
		fmt.Sprintf("%s/%s/say", chitchatBase, url.PathEscape(room)),
		"application/json",
		strings.NewReader(string(data)),
	)
	if err != nil {
		return fmt.Errorf("say: %w", err)
	}
	defer resp.Body.Close()
	return nil
}

func (c *ChitchatClient) GetMessages(room string) []ChatMessage {
	c.mu.RLock()
	defer c.mu.RUnlock()
	msgs := c.messages[room]
	if msgs == nil {
		return []ChatMessage{}
	}
	return msgs
}

func (c *ChitchatClient) MessageCount(room string) int {
	c.mu.RLock()
	defer c.mu.RUnlock()
	return len(c.messages[room])
}

func (c *ChitchatClient) GetRooms() []string {
	c.mu.RLock()
	defer c.mu.RUnlock()
	r := make([]string, len(c.rooms))
	copy(r, c.rooms)
	return r
}

func (m ChatMessage) String() string {
	ts := ""
	if len(m.TS) >= 16 {
		ts = m.TS[11:16]
	} else if len(m.TS) >= 5 {
		ts = m.TS[:5]
	}
	from := m.From
	if from == "" {
		from = "?"
	}
	return fmt.Sprintf("[%s] %s: %s", ts, from, m.Text)
}
